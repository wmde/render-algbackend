#!/usr/bin/python
# -*- coding:utf-8 -*-
# task list generator - backend
import os
import sys
import gettext
import time
import json
import Queue
import traceback
import threading
import tlgflaws
import wiki
import geobbox

from tlgcatgraph import CatGraphInterface, FindCGHost
from tlgflaws import FlawFilters
from utils import *

# todo: daemon threads?

## a worker thread which fetches actions from a queue and executes them
class WorkerThread(threading.Thread):
    def __init__(self, actionQueue, resultQueue, wikiname, runEvent):
        threading.Thread.__init__(self)
        self.actionQueue= actionQueue
        self.resultQueue= resultQueue
        self.wikiname= wikiname
        self.daemon= True
        self.runEvent= runEvent
        self.currentAction= ''
    
    def setCurrentAction(self, infoString):
        if infoString.split(':'): self.currentAction= infoString.split(':')[-1]
        else: self.currentAction= infoString
    def getCurrentAction(self):
        return self.currentAction
    
    def run(self):
        try:
            # create cursor which will most likely be used by actions later
            cur= getCursors()[self.wikiname+'_p']
            # wait until action queue is ready
            self.runEvent.wait()
            
            try:
                while True: 
                    # todo: if there are only actions left which cannot be run, exit the thread
                    action= self.actionQueue.get(True, 0)
                    if action.canExecute():
                        self.setCurrentAction(action.parent.shortname)
                        #~ dprint(1, 'executing action for %s' % action.parent.shortname)
                        action.execute(self.resultQueue)
                        #~ dprint(1, 'done.')
                    else:
                        dprint(3, "re-queueing action " + str(action) + " from %s, queue len=%d" % (action.parent.shortname, self.actionQueue.qsize()))
                        self.actionQueue.put(action)
                    
                    tempCursors= GetTempCursors()
                    rmkeys= []
                    for key in tempCursors:
                        tc= tempCursors[key]
                        if time.time() - tc.lastuse > 3:
                            dprint(0, 'closing temp cursor %s' % tc.key)
                            tc.cursor.close()
                            tc.conn.close()
                            rmkeys.append(key)
                    for k in rmkeys:
                        del tempCursors[k]
                            
            except Queue.Empty:
                self.setCurrentAction('')
                tempCursors= GetTempCursors()
                for key in tempCursors:
                    tc= tempCursors[key]
                    tc.cursor.close()
                    tc.conn.close()
                tempCursors.clear()
                # todo: close other open connections
                return
        
        except Exception:
            # unhandled exception, propagate to main thread
            self.resultQueue.put(sys.exc_info())


# replacing Queue with this lock-free container might be faster
import collections
class QueueWrapper(collections.deque):
    def init(self):
        collections.deque.__init__(self)

    def get(self, block=True, timeout=None):    # block and timeout are ignored
        try:
            return self.popleft()
        except IndexError:
            raise Queue.Empty()
    
    def put(self, item):
        self.append(item)
        
    def qsize(self):
        return len(self)
    
    def empty(self):
        return len(self)==0

        
## main app class
class TaskListGenerator:
    def __init__(self, numthreads= 10, testrun_= False):
        self.actionQueue= QueueWrapper()    #Queue.Queue()     # actions to process
        self.resultQueue= QueueWrapper()    #Queue.Queue()     # results of actions 
        self.mergedResults= {}              # final merged results, one entry per article
        self.workerThreads= []
        self.pagesToTest= []                # page IDs to test for flaws
        self.numWorkerThreads= int(numthreads)
        self.language= None                 # language code e.g. 'en'
        self.wiki= None                     # e.g. 'enwiki'
        self.cg= None
        self.runEvent= threading.Event()
        self.loadFilterModules()
        self.simpleMW= None # SimpleMW instance
        self.resultsPerFilter= {}           # shortname => resultcount
        if testrun_: enableTestrun()

    
    def getActiveWorkerCount(self):
        count= 0
        for t in self.workerThreads:
            if t.isAlive(): count+= 1
        return count
    
    @staticmethod
    def mkStatus(string):
        status= json.dumps({'status': string})
        return status
        
    def loadFilterModules(self):
        import imp
        for root, dirs, files in os.walk(os.path.join(sys.path[0], 'filtermodules')):
            for name in files:
                if name[-3:]=='.py':
                    file= None
                    module= None
                    try:
                        modname= name[:-3]
                        (file, pathname, description)= imp.find_module(modname, [root])
                        module= imp.load_module(modname, file, pathname, description)
                    except Exception as e:
                        dprint(0, "error occured while loading filter module %s, exception string was '%s'" % (modname, str(e)))
                        pass
                    finally:
                        if file: file.close()
                        if module: dprint(3, "loaded filter module '%s'" % modname)

    def getFlawList(self):
        infoString= '{\n'
        firstLine= True
        for i in sorted(FlawFilters.classInfos):
            ci= FlawFilters.classInfos[i]
            if not firstLine:
                infoString+= ',\n'
            firstLine= False
            infoString+= '\t"%s": %s' % (ci.shortname, json.dumps({ 'group': ci.group, 'label': ci.label, 'description': ci.description }))
        infoString+= '\n}\n'
        return infoString
    
    def listFlaws(self):
        print self.getFlawList()
    
    def findGeoCoordsForPage(self, page_id):
        cur= getCursors()[self.wiki+'_p']
        dprint(1, 'finding geocoords for %s on %s' % (str(page_id), str(self.wiki)))
        cur.execute('SELECT gt_lat, gt_lon FROM geo_tags WHERE gt_globe = "earth" AND gt_page_id = %s', str(page_id))
        res= cur.fetchall()
        if len(res): return [ res[0]['gt_lat'], res[0]['gt_lon'] ]
        return None
    
    def findPageIDsInGeoBBox(self, lat, lon, distance):
        bblat, bblon= geobbox.bounding_box(lat, lon, distance)
        dprint(1, "looking for articles in %s at geocoord %s,%s with max distance %s" % (self.wiki, lat, lon, distance))
        dprint(1, "bbox lat: %s, lon: %s" % (bblat, bblon))
        cur= getCursors()[self.wiki+'_p']
        cur.execute('SELECT gt_page_id FROM geo_tags WHERE gt_globe = "earth" AND gt_lat>=%s AND gt_lat<=%s AND gt_lon>=%s AND gt_lon<=%s', 
            (lat-bblat,lat+bblat, lon-bblon,lon+bblon))
        return [ row['gt_page_id'] for row in cur.fetchall() ]
    
    ## evaluate a single query category.
    # 'wl#USER,TOKEN' special syntax queries USER's watchlist instead of CatGraph.
    # 'title#PAGETITLE' returns only a single page.
    # if working on a category-only graph (self.cg_noleaves), the returned list may contain duplicates, which are removed by the conversion to a set in evalQueryString.
    def evalQueryToken(self, string, defaultdepth):
        separatorChar= '#'  # special separator char for things like 'title#PAGETITLE'
        s= string.split(separatorChar, 1)
        if len(s)==1:
            max= 1000000
            if self.cg_noleaves:
                res= self.cg.getPagesInCategory(string.replace(' ', '_'), int(defaultdepth)-1, )
                cur= getCursors()[self.wiki+'_p']
                ret= []
                # running into problems with huge result set (max_allowed_packet), so doing the query in chunks
                chunks= lambda coll,size: [ coll[i:i+size] for i in range(0,len(coll),size) ]
                for chunk in chunks(res, 500):
                    cur.execute("""select N.page_id from page as N 
                    join categorylinks on cl_from=N.page_id 
                    join page as B on B.page_title=cl_to and B.page_namespace=14 and B.page_id in (%s)""" % (",".join( [str(id) for id in chunk] )) )
                    subres= [ int(row['page_id']) for row in cur.fetchall() ]
                    ret.extend( subres )
                    dprint(1, "evalQueryToken: extended result to %s" % len(ret))
                    if len(ret) > max: 
                        dprint(1, "stopping...")
                        break
                return ret
            else:
                res= self.cg.getPagesInCategory(string.replace(' ', '_'), defaultdepth, max)
                return res
        else:
            if s[0]=='wl':  # watchlist
                wlparams= s[1].split(',')
                if len(wlparams)!=2:
                    raise InputValidationError(_('Watchlist syntax is: wl%cUSERNAME,TOKEN') % separatorChar)
                res= []
                for pageid in self.simpleMW.getWatchlistPages(wlparams[0], wlparams[1]):
                    res.append(pageid)
                return res
            
            elif s[0]=='title': # single page
                if len(s)!=2:
                    raise InputValidationError(_('Use: \'title%cPAGETITLE\'') % separatorChar)
                row= getPageByTitle(self.wiki + '_p', s[1].replace(' ', '_'), 0)
                if len(row)==0:
                    raise InputValidationError(_('Page not found in mainspace: %s') % s[1])
                return (row[0]['page_id'], )
                
            elif s[0]=='geobbox': # bounding box around geotagged page
                if len(s)!=2:
                    raise InputValidationError(_('Use: \'geobbox%cPAGETITLE,BBOXSIZE_IN_KM\'') % separatorChar)
                params= s[1].split(',')
                if len(params)<2 or len(params)>3:
                    raise InputValidationError(_('Use: \'geobbox%cPAGETITLE,BBOXSIZE_IN_KM\' or \'geobbox%cLAT,LON,BBOXSIZE_IN_KM\'') % separatorChar)
                if len(params)==2:
                    row= getPageByTitle(self.wiki + '_p', params[0].replace(' ', '_'), 0)
                    if len(row)==0:
                        raise InputValidationError(_('Page not found: %s') % params[0])
                    latlon= self.findGeoCoordsForPage(row[0]['page_id'])
                    if latlon==None:
                        raise InputValidationError("No geocoords found for '%s'" % params[0])
                    return self.findPageIDsInGeoBBox(latlon[0], latlon[1], float(params[1]))
                else:
                    return self.findPageIDsInGeoBBox(float(params[0]), float(params[1]), float(params[2]))
                
            # todo (nice-to-have): feed tlg backend output to itself as search input, shell pipe-style?
            else:
                raise InputValidationError(_('invalid query type: \'%s\'') % s[0])
    
    def evalQueryString(self, string, depth):
        result= set()
        n= 0
        for param in string.split(';'):
            param= param.strip()
            if len(param)==0:
                raise InputValidationError(_('Empty category name specified.'))
            if param[0] in '+-':
                category= param[1:].strip()
                op= param[0]
            else:
                category= param
                op= '|'
            if op=='|':
                result|= set(self.evalQueryToken(category, depth))
                if 'wl#' in category: dprint(2, ' | "%s"' % 'wl#___,___')
                else: dprint(2, ' | "%s"' % category)
            elif op=='+':
                if n==0:
                    # '+' on first category should do the expected thing
                    result|= set(self.evalQueryToken(category, depth))
                    dprint(2, ' | "%s"' % category)
                else:
                    result&= set(self.evalQueryToken(category, depth))
                    dprint(2, ' & "%s"' % category)
            elif op=='-':
                # '-' on first category has no effect
                if n!=0:
                    result-= set(self.evalQueryToken(category, depth))
                    dprint(2, ' - "%s"' % category)
            n+= 1
        result= list(result)
        if(len(result) > 1500000):
            dprint(3, "capping humungous result set (len: %d)..." % len(result))
            return result[:1500000]
        else: 
            return result
    
    
    ## find flaws (generator function).
    # @param lang The wiki language code ('de', 'fr').
    # @param queryString The query string. See CatGraphInterface.executeSearchString documentation.
    # @param queryDepth Search recursion depth.
    # @param flaws String of filter names
    def generateQuery(self, lang, queryString, queryDepth, flaws, include_hidden= False):
        try:
            begin= time.time()
            
            self.language= lang
            self.wiki= lang + 'wiki'
            self.simpleMW= wiki.SimpleMW(lang)
            self.resultsPerFilter= {}

            #~ dprint(0, 'generateQuery(): lang "%s", query string "%s", depth %s, flaws "%s"' % (lang, queryString, queryDepth, flaws))
            #~ dprint(0, 'stats: %s' % json.dumps( { 'lang': lang, 'querystring': queryString, 'depth': queryDepth, 'flaws': flaws } ))
            logStats({ 'lang': lang, 'querystring': queryString, 'depth': queryDepth, 'flaws': flaws })
            
            # spawn the worker threads
            self.initThreads()
            
            if len(queryString)==0:
                # todo: use InputValidationError exception
                yield '{"exception": "%s"}' % _('Empty category search string.')
                return
            
            yield self.mkStatus(_('evaluating query string \'%s\' with depth %d') % (queryString, int(queryDepth)))

            cghost= FindCGHost(self.wiki)
            if cghost==None:
                cghost= FindCGHost(self.wiki + '_ns14') # try to find host for category-only graph
                if cghost:
                    # category-only graph found. use it and set "noleaves" flag, which means we need to pull articles from sql
                    self.cg= CatGraphInterface(host= cghost, port= int(config['graphserv-port']), graphname= self.wiki + '_ns14')
                    self.cg_noleaves= True
                else:
                    raise RuntimeError("no catgraph host found for graph '%s'" % self.wiki)
            else:
                self.cg= CatGraphInterface(host= cghost, port= int(config['graphserv-port']), graphname= self.wiki)
                self.cg_noleaves= False
            self.pagesToTest= self.evalQueryString(queryString, queryDepth)
            
            yield self.mkStatus(_('query found %d results.') % len(self.pagesToTest))

            # todo: add something like MaxWaitTime, instead of this
            #~ if len(self.pagesToTest) > 50000:
                #~ raise RuntimeError('result set of %d pages is too large to process in a reasonable time, please modify your search string.' % len(self.pagesToTest))
            
            # create the actions for every page x every flaw
            for flawname in flaws.split():
                try:
                    flaw= FlawFilters.classInfos[flawname](self)
                except KeyError:
                    raise InputValidationError('Unknown flaw %s' % flawname)
                self.createActions(flaw, self.language, self.pagesToTest)
            
            numActions= self.actionQueue.qsize()
            yield self.mkStatus(_('%d pages to test, %d actions to process') % (len(self.pagesToTest), numActions))
            
            # signal worker threads that they can run
            self.runEvent.set()
            
            # process results as they are created
            actionsProcessed= 0 #numActions-self.actionQueue.qsize()
            while self.getActiveWorkerCount()>0 or (not self.resultQueue.empty()):
                self.drainResultQueue(include_hidden)
                n= max(numActions-self.actionQueue.qsize()-(self.getActiveWorkerCount()), 0)
                if n!=actionsProcessed:
                    actionsProcessed= n
                    eta= (time.time()-begin) / actionsProcessed * (numActions-actionsProcessed)
                    yield json.dumps( { 'progress': '%d/%d' % (actionsProcessed, numActions) } )
                    yield self.mkStatus(_('%d of %d actions processed') % (actionsProcessed, numActions))
                time.sleep(0.25)
            for i in self.workerThreads:
                i.join()
            # process the last results
            self.drainResultQueue(include_hidden, 60*60)
                        
            # sort
            sortedResults= sorted(self.mergedResults, key= lambda i: \
                (-len(self.mergedResults[i]),                                                           # length of flaw list, 
                 sorted( map(lambda x: x.FlawFilter.shortname, self.mergedResults[i]) ),                # flaw list (alphabetical), 
                 map( lambda x: x[1], sorted( map(lambda x: (x.FlawFilter.shortname, x.sortkey), 
                         self.mergedResults[i]), 
                         key= lambda x: x[1]) ),                                                        # sort key,  
                 self.mergedResults[i][0].page['page_title']))                                          # page title (alphabetical)
            
            yield self.mkStatus(_('%d pages tested in %d actions. %d pages in result set. processing took %.1f seconds. please wait while the result list is being transferred.') % \
                (len(self.pagesToTest), numActions, len(self.mergedResults), time.time()-begin))
            
            logStats({'pages_tested': len(self.pagesToTest), 'action_count': numActions, \
                'result_size': len(self.mergedResults), 'processingtime': time.time()-begin})
            
            logStats({'results_per_filter': self.resultsPerFilter })
            
            beforeYield= time.time();
            
            # print results
            for i in sortedResults:
                result= self.mergedResults[i]
                d= { 'page': result[0].page,         #['page_title'].replace('_', ' '), 
                     'flaws': map( lambda res: { 'name': res.FlawFilter.label, 'infotext': res.infotext, 'hidden': res.marked_as_done }, result )
                    }
                d['page']['page_title']= d['page']['page_title'].replace('_', ' ')
                yield json.dumps(d)
            
            logStats({'generator_yieldtime': time.time()-beforeYield})
        
        except InputValidationError as e:
            dprint(0, 'Input validation failed: %s' % str(e))
            yield '{"exception": "%s:\\n%s"}' % (_('Input validation failed'), str(e))
        
        except Exception as e:
            info= sys.exc_info()
            dprint(0, traceback.format_exc(info[2]))
            yield '{"exception": "%s"}' % (traceback.format_exc(info[2]).replace('\n', '\\n').replace('"', '\\"'))
            return
    
    ## get IDs of all the pages to be tested for flaws
    def getPageIDs(self):
        return self.pagesToTest
    
    def createActions(self, flaw, language, pagesToTest):
        pagesLeft= len(pagesToTest)
        pagesPerAction= max(1, min( flaw.getPreferredPagesPerAction(), pagesLeft/self.numWorkerThreads ))
        while pagesLeft:
            start= max(0, pagesLeft-pagesPerAction)
            flaw.createActions( self.language, pagesToTest[start:pagesLeft], self.actionQueue )
            pagesLeft-= (pagesLeft-start)
            
    #@cache_region(disk24h)
    def getMarkDBname(self):
        from getpass import getuser
        if 'project' in os.path.expanduser('~'): prefix= 'p_'
        else: prefix= 'u_'
        return '%s%s_tlgbackend' % (prefix, getuser())
    
    def processResult(self, result, include_hidden= False):
        """
        # disabled "mark-as-done" stuff, as it is not useful and disabled in the frontend anyway
        # todo: possibly re-enable per-user? (requires integration into mediawiki)
        # todo: maybe cache results and check for 'done' marks every N results
        marked= False
        try:
            with TempCursor('sql', self.getMarkDBname()) as cursor:
                cursor.execute("SELECT * FROM marked_as_done WHERE filter_name = %s AND page_latest = '%s'", (result.FlawFilter.shortname, result.page['page_latest']))
                if cursor.fetchone()!=None:
                    marked= True
        except:
            # table or db doesn't exist (yet)
            pass
        
        if marked:
            if not include_hidden: return
            result.marked_as_done= True
        """
        
        #~ dprint(1, "processResult(%s %s %s)" % (str(result.page['page_id']), str(result.page['page_title']), str(result.FlawFilter.shortname)))
        
        if not result.FlawFilter.shortname in self.resultsPerFilter:
            self.resultsPerFilter[result.FlawFilter.shortname]= 1
        else:
            self.resultsPerFilter[result.FlawFilter.shortname]+= 1

        #~ key= '%s:%d' % (result.wiki, result.page['page_id'])
        #~ try:
            #~ self.mergedResults[key].append(result)
            #~ self.mergedResults[key].sort(key= lambda x: x.FlawFilter.shortname)
        #~ except KeyError:
            #~ self.mergedResults[key]= [ result ]
        
        # workaround for file links...
        if result.page['page_namespace']==6:
            result.page['page_title']= "File:" + result.page['page_title']

        key= '%s:%s' % (result.wiki, str(result.page['page_id']))
        if not key in self.mergedResults:
            self.mergedResults[key]= [ result ]
        else:
            shortnames= [ x.FlawFilter.shortname for x in self.mergedResults[key] ]
            if result.FlawFilter.shortname in shortnames:
                #~ dprint(1, 'omitting duplicate %s result' % result.FlawFilter.shortname)
                pass
            else:
                self.mergedResults[key].append(result)
                self.mergedResults[key].sort(key= lambda x: x.FlawFilter.shortname)
            #~ for x in self.mergedResults[key]:
                #~ if x.FlawFilter.shortname == result.FlawFilter.shortname:
                    #~ dprint(1, 'omitting duplicate %s result for %s' % (result.FlawFilter.shortname, result.page['page_title']))
                    #~ return
            #~ dprint(1, "adding result for %s" % result.FlawFilter.shortname)
            #~ self.mergedResults[key].append(result)
            #~ self.mergedResults[key].sort(key= lambda x: x.FlawFilter.shortname)

    def processWorkerException(self, exc_info):
        raise exc_info[0], exc_info[1], exc_info[2] # re-throw exception from worker thread
        
    def drainResultQueue(self, include_hidden, timeout=2):
        starttime= time.time()
        while not self.resultQueue.empty() and time.time()-starttime<timeout:
            result= self.resultQueue.get()
            if isinstance(result, tlgflaws.TlgResult): self.processResult(result, include_hidden)
            else: self.processWorkerException(result)

    # create and start worker threads
    def initThreads(self):
        for i in range(0, self.numWorkerThreads):
            self.workerThreads.append(WorkerThread(self.actionQueue, self.resultQueue, self.wiki, self.runEvent))
            self.workerThreads[-1].start()

    def markAsDone(self, pageID, pageTitle, pageRev, filterName, unmark):
        from getpass import getuser
        import MySQLdb
        dbname= self.getMarkDBname()
        tablename= 'marked_as_done'
        conn= MySQLdb.connect(read_default_file=os.path.expanduser('~')+"/.my.cnf", host='sql', use_unicode=False, cursorclass=MySQLdb.cursors.DictCursor)
        cursor= conn.cursor()
        cursor.execute('CREATE DATABASE IF NOT EXISTS %s' % conn.escape_string(dbname))
        cursor.execute('USE %s' % conn.escape_string(dbname))
        cursor.execute("""CREATE TABLE IF NOT EXISTS %s (
            page_id INT(10) UNSIGNED,
            page_title VARBINARY(255),
            page_latest INT(10) UNSIGNED,
            filter_name VARBINARY(255),
            UNIQUE KEY (page_latest, filter_name),
            KEY (page_id),
            KEY (page_title),
            KEY (page_latest),
            KEY (filter_name))""" % tablename)
        if unmark:
            cursor.execute('DELETE FROM ' + tablename + " WHERE page_latest = %s AND filter_name = %s", 
                (pageRev, filterName))
        else:
            cursor.execute('REPLACE INTO ' + tablename + ' VALUES (%s, %s, %s, %s)', 
                (pageID, pageTitle, pageRev, filterName))
        conn.commit()
        cursor.close()
        conn.close()


class test:
    def __init__(self):
        self.tlg= TaskListGenerator()

    def createActions(self):
        cg= CatGraphInterface(graphname='dewiki')
        pages= cg.executeSearchString('Biologie -Meerkatzenverwandte -Astrobiologie', 2)
        
        flaw= tlgflaws.FFUnlucky()
        for k in range(0, 3):
            for i in pages:
                action= flaw.createAction( 'de', (i,) )
                self.tlg.actionQueue.put(action)
    
    def drainResultQueue(self):
        try:
            while not self.tlg.resultQueue.empty():
                foo= self.tlg.resultQueue.get()
                print foo.encodeAsJSON()
        except (UnicodeEncodeError, UnicodeDecodeError) as exception:  # wtf?!
            raise

    def testSingleThread(self):
        self.createActions()
        numActions= self.tlg.actionQueue.qsize()
        WorkerThread(self.tlg.actionQueue, self.tlg.resultQueue).run()
        self.drainResultQueue()
        print "numActions=%d" % numActions
        sys.stdout.flush()

    def testMultiThread(self, nthreads):
        self.createActions()
        numActions= self.tlg.actionQueue.qsize()
        for i in range(0, nthreads):
            dprint(0, "******** before thread start %d" % i)
            self.tlg.workerThreads.append(WorkerThread(self.tlg.actionQueue, self.tlg.resultQueue))
            self.tlg.workerThreads[-1].start()
        while threading.activeCount()>1:
            self.drainResultQueue()
            time.sleep(0.5)
        for i in self.tlg.workerThreads:
            i.join()
        self.drainResultQueue()
        print "numActions=%d" % numActions
        sys.stdout.flush()



if __name__ == '__main__':
    gettext.translation('tlgbackend', localedir= os.path.join(sys.path[0], 'messages'), languages=['de']).install()
    #~ TaskListGenerator().listFlaws()
    #~ TaskListGenerator().run('de', 'Biologie +Eukaryoten -Rhizarien', 5, 'PageSize')
    #~ for line in TaskListGenerator().generateQuery('de', 'Biologie; +Eukaryoten; -Rhizarien', 4, 'NoImages'):
    for line in TaskListGenerator().generateQuery('fr', 'Plante fruitière', 2, 'ALL'):
    #~ for line in TaskListGenerator().generateQuery('de', 'Politik; +Physik', 3, 'ALL'):
    #~ for line in TaskListGenerator().generateQuery('de', '+wl:Johannes Kroll (WMDE),xxxxx', 3, 'ALL'):
        print line
        sys.stdout.flush()
    

