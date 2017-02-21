#!/usr/bin/python
# -*- coding:utf-8 -*-
import os
import socket
from wikitools import wiki, api
from utils import *

class SimpleMW:
    ## constructor.
    # @param lang language ('de', 'en' etc)
    def __init__(self, lang):
        try:
            if str(lang)=='commons':
                host= "commons.wikimedia.org"
            else:
                host= "%s.wikipedia.org" % str(lang)
            # wikitools.wiki.Wiki hangs with 'Name or service not known trying request again in X seconds" on non-existent hosts, 
            # so catch this condition here and generate exception on dns lookup failure
            socket.getaddrinfo(host, 80) 
            self.site= wiki.Wiki("https://%s/w/api.php" % host)
            self.site.setUserAgent('TLGBackend/0.1 (http://tools.wmflabs.org/render/tlgbe/tlgwsgi.py)')
            self.site.cookiepath= os.path.expanduser('~')+'/.tlgbackend/'
            try: os.mkdir(self.site.cookiepath)
            except: pass    # assume it's already there
            self.edittoken= False
        except UnicodeEncodeError:  # FIXME/HACK happens for lang 'es' and possibly others. bug in wikitools?
            dprint(0, '*** FIXME UnicodeEncodeError in wikitools')
            info= sys.exc_info()
            import traceback
            dprint(0, traceback.format_exc(info[2])) 
    
    def tryLogin(self):
        if self.site.isLoggedIn():
            return
        try:
            self.login()
        except UnicodeEncodeError:  # FIXME/HACK happens for lang 'es' and possibly others. bug in wikitools?
            dprint(0, '*** FIXME UnicodeEncodeError in wikitools')
            info= sys.exc_info()
            import traceback
            dprint(0, traceback.format_exc(info[2])) 
        
    
    ## login to the api.
    # uses cookie file to remember previous login.
    def login(self):
        if not self.site.isLoggedIn():
            # todo: put credentials into config file :)
            dprint(1, 'login: %s' % self.site.login('tlgbackend', 'wck8#0g', remember= True))
        else:
            dprint(1, 'login: already logged in')

    ## get edit token.
    def getEditToken(self):
        self.tryLogin()
        if not self.edittoken: 
            params= {   'action': 'tokens',
                        'type': 'edit',
            }
            req= api.APIRequest(self.site, params)
            res= req.query(querycontinue=False)
            self.edittoken= res['tokens']['edittoken']
        return self.edittoken

    ## write query result to a wiki page. wip.
    def writeToPage(self, queryString, queryDepth, flaws, outputIterable, action, wikipage):
        self.tryLogin()
        edittext= ''
        edittext+= _('= Task List =\n')
        edittext+= _('Categories: %s, Search depth: %s, Selected filters: %s\n') % (queryString, queryDepth, flaws)
        
        for i in outputIterable: edittext+= str(i)

        params= {   'action': 'edit',
                    'title': wikipage,
                    'text': edittext,
                    'summary': 'edit summary.',
                    'bot': 'True',
                    'recreate': 'True',
                    'token': self.getEditToken(),
                }
        req= api.APIRequest(self.site, params, write= True)
        res= req.query(querycontinue= False)
        if not 'edit' in res or not 'result' in res['edit'] or res['edit']['result']!='Success':
            raise RuntimeError(str(res))
        return res
    
    ## get complete watchlist, starting from wlstart.
    # only retrieves pages in namespace 0 (articles).
    def getWatchlist(self, wlowner, wltoken, wlstart= None, wlend= None):
        self.tryLogin()
        params= {   'action': 'query',
                    'list': 'watchlist',
                    'wlowner': wlowner,
                    'wltoken': wltoken,
                    'wlprop': 'user|timestamp|title|ids|flags',
                    'wlallrev': 'true',
                    'wldir': 'older',
                    'wlnamespace': 0,
                }
        
        if wlstart: params['wlstart']= wlstart
        if wlend: params['wlend']= wlend

        try:
            req= api.APIRequest(self.site, params)
            res= req.query(querycontinue= True)
            #~ if not 'edit' in res or not 'result' in res['edit'] or res['edit']['result']!='Success':
                #~ raise RuntimeError(str(res))
            return res
        except api.APIError as e:
            raise InputValidationError('%s\\n%s' % (e[0], e[1]))
    
    
    def getWatchlistPages(self, wlowner, wltoken, wlstart= None, wlend= None):
        self.tryLogin()
        wl= self.getWatchlist(wlowner, wltoken, wlstart, wlend)
        res= dict()
        for p in wl['query']['watchlist']:
            if p['pageid']!=0 and (not(p['pageid'] in res) or (res[p['pageid']]['timestamp'] < p['timestamp'])):
                res[p['pageid']]= p
        return res
    
        

if __name__ == '__main__':
    global _
    def ident(x): return x
    _= ident
    from pprint import pprint
    
    # XXX todo: use config files
    
    mw= SimpleMW('de')
    #~ print mw.writeToPage('query string', 3, 'filter1 filter2 filter3', ('foo\n\n', 'bar\n\n', 'baz\n\n', 'etc\n\n'), 'query', 'Benutzer:Tlgbackend/Foo')
    #~ pprint(mw.getWatchlist('Johannes Kroll (WMDE)', ''))
    pprint(mw.getWatchlistPages('Johannes Kroll (WMDE)', ''))
    
    sys.exit(0)
    
    
    import pprint # Used for formatting the output for viewing, not necessary for most code
    site= wiki.Wiki("http://de.wikipedia.org/w/api.php")
    
    print ' *** login'
    params= {   'action': 'login',
                'lgname': 'tlgbackend',
                'lgpassword': 'wck8#0g'
    }
    
    req= api.APIRequest(site, params)
    res= req.query(querycontinue=False)
    
    token= res['login']['token']

    print ' *** lgtoken'
    params= {   'action': 'login',
                'lgname': 'tlgbackend',
                'lgpassword': 'wck8#0g',
                'lgtoken': token,
    }
    
    req= api.APIRequest(site, params)
    res= req.query(querycontinue=False)
    
    print ' *** edittoken'
    params= {   'action': 'tokens',
                'type': 'edit',
    }
    
    req= api.APIRequest(site, params)
    res= req.query(querycontinue=False)
    pprint.pprint(res)
    
    edittoken= res['tokens']['edittoken']
    
    print ' *** edit'
    params= {   'action': 'edit',
                'title': 'Benutzer:Tlgbackend/Foo',
                'text': 'Test Text!',
                'summary': 'edit summary.',
                'bot': 'True',
                'recreate': 'True',
                'token': edittoken,
            }
    req= api.APIRequest(site, params, write= True)
    res= req.query(querycontinue=False)
    print(res)

