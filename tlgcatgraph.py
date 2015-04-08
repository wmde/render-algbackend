#!/usr/bin/python
# task list generator - interface to catgraph
import time
import requests
from gp import *
from utils import *

def FindCGHost(graphname):
    r= requests.get('http://sylvester/hostmap/%s' % graphname)
    if r.status_code==200:
        return r.text
    return None

class CatGraphInterface:
    def __init__(self, host='ortelius.toolserver.org', port=6666, graphname=None):
        self.gp= client.Connection( client.ClientTransport(host, port), graphname )
        self.gp.connect()
        self.graphname= graphname
        self.wikiname= (graphname.split('_')[0] if graphname.endswith('_ns14') else graphname)  + '_p' 
    
    def getPagesInCategory(self, category, depth=2, max=None):
        catID= getCategoryID(self.wikiname, category)
        if catID!=None:
            result= []
            if max:
                successors= self.gp.capture_traverse_successors(catID, str(depth), str(max))
            else:
                successors= self.gp.capture_traverse_successors(catID, str(depth))
            if successors:  # result can be None for empty categories
                # convert list of tuples to simple list. is there a faster (i.e. built-in) way to do this?
                for i in successors:
                    result.append(i[0])
            return result
        else:
            # category not found. 
            raise InputValidationError(_('Category %s not found in database %s.') % (category, self.wikiname))
    
    ## execute a search engine-style string
    #  operators '+' (intersection) and '-' (difference) are supported
    #  e. g. "Biology; Art; +Apes; -Cats" searches for everything in Biology or Art and in Apes, not in Cats
    #  search parameters are evaluated from left to right, i.e. results might differ depending on order.
    #  on the first category, any '+' operator is ignored, while a '-' operator yields an empty result.
    #  the "depth" parameter is applied to each category.
    #  @param string The search string.
    #  @param depth The search depth.
    # --- this method isn't used in ALG any more, see tlgbackend.py/evalQueryString instead
    def executeSearchString(self, string, depth):
        # todo: something like "Category|3" to override search depth
        # todo: it would be cool to have this command in graphcore, possibly using threads for each category.
        result= set()
        n= 0
        for param in string.split(';'):
            param= param.strip()
            if len(param)==0:
                raise InputValidationError(_('Empty category name specified.'))
            if param[0] in '+-':
                category= param[1:].strip().replace(' ', '_')
                op= param[0]
            else:
                category= param.replace(' ', '_')
                op= '|'
            if op=='|':
                result|= set(self.getPagesInCategory(category, depth))
                dprint(2, ' | "%s"' % category)
            elif op=='+':
                if n==0:
                    # '+' on first category should do the expected thing
                    result|= set(self.getPagesInCategory(category, depth))
                    dprint(2, ' | "%s"' % category)
                else:
                    result&= set(self.getPagesInCategory(category, depth))
                    dprint(2, ' & "%s"' % category)
            elif op=='-':
                # '-' on first category has no effect
                if n!=0:
                    result-= set(self.getPagesInCategory(category, depth))
                    dprint(2, ' - "%s"' % category)
            n+= 1
        return list(result)
        

if __name__ == '__main__':
    cg= CatGraphInterface(graphname='dewiki')
    #~ catID= getCategoryID(cg.wikiname, '!Hauptkategorie')
    #~ print cg.gp.capture_traverse_successors(catID, 1)
    
    depth= 5
    
    t= time.time()
    for category in ['Biologie', 'Katzen', 'Foo', 'Astrobiologie']:
        catID= getCategoryID('dewiki_p', category)
        if catID:
            cg.gp.capture_traverse_successors(catID, depth)
    traw= time.time()-t
    
    search= '+Biologie; -Katzen; -Astrobiologie; Foo'
    print "searching for '%s'..." % search
    sys.stdout.flush()
    t= time.time()
    set= cg.executeSearchString(search, depth)
    #print set
    print "search found %d pages" % (len(set))
    tcooked= time.time()-t
    
    print "traw: %s tcooked: %s" % (traw, tcooked)
    
