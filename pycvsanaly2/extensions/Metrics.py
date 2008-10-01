# Copyright (C) 2008 LibreSoft
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Library General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Authors :
#       Israel Herraiz <herraiz@gsyc.escet.urjc.es>
#       Carlos Garcia Campos  <carlosgc@gsyc.escet.urjc.es>

# Description
# -----------
# This extension calculates some metrics for all the different
# versions of all the files stored in the control version system.
#
# It needs the FilePaths extension to be called first.

from repositoryhandler.backends import create_repository
from pycvsanaly2.Database import (SqliteDatabase, MysqlDatabase, TableAlreadyExists,
                                  statement, DBFile)
from pycvsanaly2.extensions import Extension, register_extension, ExtensionRunError
from pycvsanaly2.Config import Config
from pycvsanaly2.utils import (printdbg, printerr, printout, remove_directory,
                               get_path_for_revision, path_is_deleted_for_revision)
from pycvsanaly2.FindProgram import find_program
from pycvsanaly2.profile import profiler_start, profiler_stop
from tempfile import mkdtemp
from xml.sax import handler as xmlhandler, make_parser
import os
import commands
import re

class ProgramNotFound (Extension):

    def __init__ (self, program):
        self.program = program

class Measures:

    def __init__ (self):
        self.__dict__ = {
            'lang'           : 'unknown',
            'loc'            : None,
            'sloc'           : None,
            'ncomment'       : None,
            'lcomment'       : None,
            'lblank'         : None,
            'nfunctions'     : None,
            'mccabe_max'     : None,
            'mccabe_min'     : None,
            'mccabe_sum'     : None,
            'mccabe_mean'    : None,
            'mccabe_median'  : None,
            'halstead_length': None,
            'halstead_vol'   : None,
            'halstead_level' : None,
            'halstead_md'    : None,
        }

    def __getattr__ (self, name):
        return self.__dict__[name]

    def __setattr__ (self, name, value):
        self.__dict__[name] = value

    def getattrs (self):
        return self.__dict__.keys ()

class FileMetrics:

    def __init__ (self, path, lang='unknown', sloc=0):
        self.path = path
        self.lang = lang
        self.sloc = sloc
    
    def get_LOC (self):
        """Measures LOC using Python file functions"""
        
        fileobj = open (self.path, 'r')
        loc = len (fileobj.readlines ())
        fileobj.close ()
        
        return loc

    def get_SLOCLang (self):
        return self.sloc, self.lang

    def get_CommentsBlank (self):
        raise NotImplementedError

    def get_HalsteadComplexity (self):
        raise NotImplementedError

    def get_MccabeComplexity (self):
        raise NotImplementedError

    def _get_mccabe_stats (nfunctions, mccabe_values):
        # There is a mccabe value for each function
        # This calculates some summary statistics for that set of
        # values
        mccabe_sum = sum (mccabe_values)
        if nfunctions >= 1:
            mccabe_mean = mccabe_sum / nfunctions
            
        mccabe_min = min (mccabe_values)
        mccabe_max = max (mccabe_values)

        # Calculate median
        mccabe_values.sort ()
        if nfunctions == 1:
            mccabe_median = mccabe_mean
        elif nfunctions >= 2:
            n = len (mccabe_values)
            if nfunctions & 1:
                mccabe_median = mccabe_values[n // 2]
            else:
                mccabe_median = (mccabe_values[n // 2 - 1] + mccabe_values[n // 2]) / 2

        return mccabe_sum, mccabe_min, mccabe_max, mccabe_mean, mccabe_median
    
    _get_mccabe_stats = staticmethod (_get_mccabe_stats)
    
class FileMetricsC (FileMetrics):
    """Measures McCabe's complexity, Halstead's complexity,
    comment and blank lines, using the 'metrics' package by Brian
    Renaud, stored in the Libresoft's subversion repository."""
    
    def get_CommentsBlank (self):
        kdsi = find_program ('kdsi')
        if kdsi is None:
            raise ProgramNotFound ('kdsi')
        
        # Running kdsi
        kdsicmd = kdsi + " " + self.path
        outputtext = commands.getoutput (kdsicmd)
        # Get rid of all the spaces and get a list
        output_values = [x for x in outputtext.split (' ') if '' != x]
        # sloc will be ignored, but it is also generated by the tool
        dummy, blank_lines, comment_lines, comment_number, dummy = output_values

        return comment_number, comment_lines, blank_lines

    def get_HalsteadComplexity (self):
        halstead = find_program ('halstead')
        if halstead is None:
            raise ProgramNotFound ('halstead')
        
        # Running halstead
        halsteadcmd = halstead + " " + self.path
        outputtext = commands.getoutput (halsteadcmd)
        values = outputtext.split ('\t')

        filename = values[0]
        try:
            halstead_length = int (values[1])
        except:
            halstead_length = None
        try:
            halstead_volume = int (values[2])
        except:
            halstead_volume = None
        try:
            halstead_level = float (values[3].replace (',', '.'))
            if str (halstead_level) == str (float ('inf')) \
                or str (halstead_level) == str (float ('nan')):
                    halstead_level = None
        except:
            halstead_level = None
        try:
            halstead_md = int (values[4])
        except:
            halstead_md = None

        return halstead_length, halstead_volume, halstead_level, halstead_md

    def get_MccabeComplexity (self):
        mccabe = find_program ('mccabe')
        if mccabe is None:
            raise ProgramNotFound ('mccabe')
        
        # Running mccabe
        mccabecmd = mccabe + " -n " + self.path
        # The output of this tool is multiline (one line per function)
        outputlines = commands.getoutput (mccabecmd).split ('\n')
        mccabe_values = []
        nfunctions = 0
        mccabe_sum = mccabe_min = mccabe_max = mccabe_mean = mccabe_median = None
        for l in outputlines:
            values = l.split ('\t')
            if len (values) != 5:
                continue

            try:
                mccabe = int (values[-2])
            except:
                mccabe = 0
                
            nfunctions += 1
            mccabe_values.append (mccabe)

        if mccabe_values:
            mccabe_sum, mccabe_min, mccabe_max, \
                mccabe_mean, mccabe_median = self._get_mccabe_stats (nfunctions, mccabe_values)
        else:
            nfunctions = None
            
        return mccabe_sum, mccabe_min, mccabe_max, mccabe_mean, mccabe_median, nfunctions
                

class FileMetricsPython (FileMetrics):

    patterns = {}
    patterns['numComments'] = re.compile ("^[ \b\t]+([0-9]+)[ \b\t]+numComments$")
    patterns['mccabe'] = re.compile ("^[ \b\t]+([0-9]+)[ \b\t]+(.*)$")
    
    def __init__ (self, path, lang='unknown', sloc=0):
        FileMetrics.__init__ (self, path, lang, sloc)

        self.pymetrics = None

    def __ensure_pymetrics (self):
        if self.pymetrics is not None:
            return

        self.pymetrics = find_program ('pymetrics')
        if self.pymetrics is None:
            raise ProgramNotFound ('pymetrics')
        
    def get_CommentsBlank (self):
        self.__ensure_pymetrics ()

        command = self.pymetrics + ' -C -S -i simple:SimpleMetric ' + self.path
        outputlines = commands.getoutput (command).split ('\n')
        comment_number = comment_lines = blank_lines = None
        for line in outputlines:
            m = self.patterns['numComments'].match (line)
            if m:
                comment_lines = m.group (1)
                continue
                
        return comment_number, comment_lines, blank_lines
    
    def get_MccabeComplexity (self):
        self.__ensure_pymetrics ()

        command = self.pymetrics + ' -C -S -B -i mccabe:McCabeMetric ' + self.path
        outputlines = commands.getoutput (command).split ('\n')
        mccabe_values = []
        nfunctions = 0
        mccabe_sum = mccabe_min = mccabe_max = mccabe_mean = mccabe_median = None
        for line in outputlines:
            m = self.patterns['mccabe'].match (line)
            if m:
                nfunctions += 1
                try:
                    mccabe = int (m.group (1))
                except:
                    mccabe = 0
                mccabe_values.append (mccabe)

        if mccabe_values:
            mccabe_sum, mccabe_min, mccabe_max, \
                mccabe_mean, mccabe_median = self._get_mccabe_stats (nfunctions, mccabe_values)
        else:
            nfunctions = None
                
        return mccabe_sum, mccabe_min, mccabe_max, mccabe_mean, mccabe_median, nfunctions                              

class FileMetricsCCCC (FileMetrics):
    # Abstract class
    
    cccc_lang = None
    
    class XMLMetricsHandler (xmlhandler.ContentHandler):
        
        def __init__ (self):
            self.comment_lines = 0
            self.nfunctions = 0
            self.mccabe_values = []

            self.current = None

        def startElement (self, name, attributes):
            if name == 'project_summary':
                self.current = name
            elif name == 'lines_of_comment' and self.current == 'project_summary':
                self.comment_lines = int (attributes['value'])
            elif name == 'module':
                self.current = name
                self.nfunctions += 1
            elif name == 'McCabes_cyclomatic_complexity' and self.current == 'module':
                self.mccabe_values.append (int (attributes['value']))
                
        def endElement (self, name):
            if name == 'project_summary' or name == 'module':
                self.current = None
    
    def __init__ (self, path, lang='unknown', sloc=0):
        FileMetrics.__init__ (self, path, lang, sloc)

        self.handler = None

    def __ensure_handler (self):
        if self.handler is not None:
            return

        cccc = find_program ('cccc')
        if cccc is None:
            raise ProgramNotFound ('cccc')

        tmpdir = mkdtemp ()
        
        command = cccc + ' --outdir=' + tmpdir + ' --lang=' + self.cccc_lang + ' ' + self.path
        status, dummy = commands.getstatusoutput (command)

        self.handler = FileMetricsCCCC.XMLMetricsHandler ()
        fd = open (os.path.join (tmpdir, 'cccc.xml'), 'r')

        parser = make_parser ()
        parser.setContentHandler (self.handler)
        parser.feed (fd.read ())

        fd.close ()

        remove_directory (tmpdir)

    def get_CommentsBlank (self):
        self.__ensure_handler ()

        return None, self.handler.comment_lines, None

    def get_MccabeComplexity (self):
        self.__ensure_handler ()

        mccabe_sum = mccabe_min = mccabe_max = mccabe_mean = mccabe_median = None
        nfunctions = self.handler.nfunctions
        if self.handler.mccabe_values:
            mccabe_sum, mccabe_min, mccabe_max, \
                mccabe_mean, mccabe_median = self._get_mccabe_stats (self.handler.nfunctions, self.handler.mccabe_values)
        else:
            nfunctions = None
                
        return mccabe_sum, mccabe_min, mccabe_max, mccabe_mean, mccabe_median, nfunctions                                      
    
class FileMetricsCPP (FileMetricsCCCC):

    cccc_lang = 'c++'

class FileMetricsJava (FileMetricsCCCC):

    cccc_lang = 'java'


_metrics = {
    "unknown" : FileMetrics,
    "ansic"   : FileMetricsC,
    "python"  : FileMetricsPython,
    "cpp"     : FileMetricsCPP,
    "java"    : FileMetricsJava
}
    
def create_file_metrics (path):
    """Measures SLOC and identifies programming language using SlocCount"""

    sloc = 0
    lang = 'unknown'
    
    sloccount = find_program ('sloccount')
    if sloccount is not None:
        sloccountcmd = sloccount + ' --wide --details ' + path
        outputlines = commands.getoutput (sloccountcmd).split ('\n')

        for l in outputlines:
            # If there is not 'top_dir', then ignore line
            if '\ttop_dir\t' in l:
                sloc, lang, unused1, unused2 = l.split ('\t')

            # If no line with 'top_dir' is found, that means
            # that SLOC is 0 and lang is unknown
        
    fm = _metrics.get (lang, FileMetrics)
    return fm (path, lang, sloc)

class Metrics (Extension):

    deps = ['FilePaths', 'FileTypes']

    # How many times it'll retry
    # when an update or chekcout fails
    RETRIES = 1

    # Insert query
    __insert__ = 'INSERT INTO metrics (id, file_id, commit_id, lang, sloc, loc, ncomment, ' + \
                 'lcomment, lblank, nfunctions, mccabe_max, mccabe_min, mccabe_sum, mccabe_mean, ' + \
                 'mccabe_median, halstead_length, halstead_vol, halstead_level, halstead_md) ' + \
                 'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'
    MAX_METRICS = 100

    def __init__ (self):
        self.db = None
        self.config = Config ()
        self.metrics = []
    
    def __create_table (self, cnn):
        cursor = cnn.cursor ()

        if isinstance (self.db, SqliteDatabase):
            import pysqlite2.dbapi2
            
            try:
                cursor.execute ("CREATE TABLE metrics (" +
                                "id integer primary key," +
                                "file_id integer," +
                                "commit_id integer," +
                                "lang text," +
                                "sloc integer," +
                                "loc integer," +
                                "ncomment integer," +
                                "lcomment integer," +
                                "lblank integer," +
                                "nfunctions integer," +
                                "mccabe_max integer," +
                                "mccabe_min integer," +
                                "mccabe_sum integer," +
                                "mccabe_mean integer," +
                                "mccabe_median integer," +
                                "halstead_length integer,"+
                                "halstead_vol integer," +
                                "halstead_level double,"+
                                "halstead_md integer" +
                                ")")
            except pysqlite2.dbapi2.OperationalError:
                cursor.close ()
                raise TableAlreadyExists
            except:
                raise
        elif isinstance (self.db, MysqlDatabase):
            import _mysql_exceptions

            try:
                cursor.execute ("CREATE TABLE metrics (" +
                                "id integer primary key not null," +
                                "file_id integer," +
                                "commit_id integer," +
                                "lang tinytext," +
                                "sloc integer," +
                                "loc integer," +
                                "ncomment integer," +
                                "lcomment integer," +
                                "lblank integer," +
                                "nfunctions integer," +
                                "mccabe_max integer," +
                                "mccabe_min integer," +
                                "mccabe_sum integer," +
                                "mccabe_mean integer," +
                                "mccabe_median integer," +
                                "halstead_length integer,"+
                                "halstead_vol integer," +
                                "halstead_level double,"+
                                "halstead_md integer," +
                                "FOREIGN KEY (file_id) REFERENCES tree(id)," +
                                "FOREIGN KEY (commit_id) REFERENCES scmlog(id)" +
                                ") CHARACTER SET=utf8")
            except _mysql_exceptions.OperationalError, e:
                if e.args[0] == 1050:
                    cursor.close ()
                    raise TableAlreadyExists
                raise
            except:
                raise
            
        cnn.commit ()
        cursor.close ()

    def __get_metrics (self, cnn):
        cursor = cnn.cursor ()
        cursor.execute (statement ("SELECT file_id, commit_id from metrics", self.db.place_holder))
        metrics = [(res[0], res[1]) for res in cursor.fetchall ()]
        cursor.close ()
        
        return metrics

    def __checkout (self, repo, uri, rootdir, newdir = None, rev = None):
        count = 0

        if newdir is not None:
            file_path = os.path.join (rootdir, newdir)
        else:
            file_path = os.path.join (rootdir, uri)
        
        while self.RETRIES - count >= 0:
            repo.checkout (uri, rootdir, newdir=newdir, rev=rev)
                
            try:
                new_rev = repo.get_last_revision (file_path)
                if rev == new_rev:
                    break
                printout ("Warning: checkout %s@%s failed in try %d: got %s.", (uri, rev, count + 1, new_rev))
            except Exception, e:
                printout ("Warning: checkout %s@%s failed in try %d: %s", (uri, rev, count + 1, str (e)))
            
            count += 1

    def __update (self, repo, uri, rev):
        count = 0

        while self.RETRIES - count >= 0:
            repo.update (uri, rev=rev, force=True)
            try:
                new_rev = repo.get_last_revision (uri)
                if rev == new_rev:
                    break
                printout ("Warning: update %s@%s failed in try %d: got %s.", (uri, rev, count + 1, new_rev))
            except Exception, e:
                printout ("Warning: update %s@%s failed in try %d: %s", (uri, rev, count + 1, str (e)))

            count += 1

    def __insert_many (self, cursor):
        if not self.metrics:
            return
        
        cursor.executemany (statement (self.__insert__, self.db.place_holder), self.metrics)
        self.metrics = []
        
    def run (self, repo, db):
        profiler_start ("Running Metrics extension")
        
        self.db = db

        cnn = self.db.connect ()
        id_counter = 1
        metrics = []

        try:
            self.__create_table (cnn)
        except TableAlreadyExists:
            cursor = cnn.cursor ()
            cursor.execute (statement ("SELECT max(id) from metrics", db.place_holder))
            id = cursor.fetchone ()[0]
            if id is not None:
                id_counter = id + 1
            cursor.close ()

            metrics = self.__get_metrics (cnn)
        except Exception, e:
            raise ExtensionRunError (str(e))

        read_cursor = cnn.cursor ()
        write_cursor = cnn.cursor ()

        uri = repo.get_uri ()
        type = repo.get_type ()
        read_cursor.execute (statement ("SELECT id from repositories where uri = ?", db.place_holder), (uri,))
        repoid = read_cursor.fetchone ()[0]

        # Temp dir for the checkouts
        tmpdir = mkdtemp ()
            
        # SVN needs the first revision
        if type == 'svn':
            topdirs = []
                
            # Get top level dirs of the repo
            query =  'SELECT tree.file_name, tree.id, MIN(scmlog.rev) '
            query += 'FROM scmlog, actions, tree '
            query += 'WHERE actions.commit_id = scmlog.id '
            query += 'AND actions.file_id = tree.id '
            query += 'AND tree.parent = -1 '
            if not self.config.metrics_all:
                query += 'AND tree.id not in (SELECT file_id from actions '
                query += 'WHERE type = "D" and head) '
            query += 'AND scmlog.repository_id = ? '
            query += 'GROUP BY tree.id;'
                
            read_cursor.execute (statement (query, db.place_holder), (repoid,))
                
            for topdir, topdir_id, first_rev in read_cursor.fetchall ():
                topdirs.append ((topdir, first_rev))
                aux_cursor = cnn.cursor ()
                aux_topdir = get_path_for_revision (topdir, topdir_id, first_rev, aux_cursor, db.place_holder).strip ('/')
                aux_cursor.close ()
                try:
                    profiler_start ("Checking out toplevel %s", (topdir,))
                    self.__checkout (repo, aux_topdir, tmpdir, newdir=topdir, rev=first_rev)
                    profiler_stop ("Checking out toplevel %s", (topdir,))
                except Exception, e:
                    msg = 'SVN checkout first rev (%s) failed. Error: %s' % (str (first_rev), 
                                                                                 str (e))
                    raise ExtensionRunError (msg)
                
            printdbg ('SVN checkout first rev finished')

        # Obtain files and revisions
        query =  'SELECT rev, path, a.commit_id, a.file_id, composed_rev '
        query += 'FROM scmlog s, actions a, file_paths f, file_types t '
        query += 'WHERE a.commit_id=s.id '
        query += 'AND a.file_id=f.id '
        query += 'AND a.file_id=t.file_id '
        query += 'AND a.type in ("M", "A") '
        query += 'AND t.type in ("code", "unknown") '
        if not self.config.metrics_all:
            query += 'AND a.head '
        query += 'AND s.repository_id=? '
        query += 'ORDER BY s.date DESC'

        current_revision = None
        read_cursor.execute (statement (query, db.place_holder), (repoid,))
        for revision, filepath, commit_id, file_id, composed in read_cursor.fetchall ():
            if (file_id, commit_id) in metrics:
                continue
                
            if composed:
                rev = revision.split ("|")[0]
            else:
                rev = revision
                    
            relative_path = filepath
                
            if type != 'cvs': # There aren't moved or renamed paths in CVS
                profiler_start ("Check if the path has been deleted")
                deleted = path_is_deleted_for_revision (relative_path, file_id, rev, read_cursor, db.place_holder)
                profiler_stop ("Check if the path has been deleted")
                if deleted:
                    printdbg ("Path %s is deleted in revision %s, skipping", (relative_path, rev))
                    continue
                    
                profiler_start ("Getting path for the given revision")
                relative_path = get_path_for_revision (relative_path, file_id, rev, read_cursor, db.place_holder).strip ('/')
                printdbg ("File path %s is relative path %s on revision %s", (filepath, relative_path, rev))
                profiler_stop ("Getting path for the given revision")
                
            if revision != current_revision:
                try:
                    if type == 'svn':
                        for topdir, first_rev in topdirs:
                            if relative_path == topdir and rev == first_rev:
                                # We already have such revision from the initial checkout
                                continue
                            if not relative_path.startswith (topdir):
                                continue
                            printdbg ("Updating tree %s to revision %s", (topdir, rev))
                            profiler_start ("Updating tree %s to revision %s", (topdir, rev))
                            self.__update (repo, os.path.join (tmpdir, topdir), rev=rev)
                            profiler_stop ("Updating tree %s to revision %s", (topdir, rev))
                    else:
                        printdbg ("Checking out %s @ %s", (relative_path, rev))
                        profiler_start ("Checking out %s @ %s", (relative_path, rev))
                        self.__checkout (repo, relative_path, tmpdir, rev=rev)
                        profiler_stop ("Checking out %s @ %s", (relative_path, rev))
                except Exception, e:
                    printerr ("Error obtaining %s@%s. Exception: %s", (relative_path, rev, str (e)))
            
                current_revision = revision

            checkout_path = os.path.join (tmpdir, relative_path)
            if os.path.isdir (checkout_path):
                continue

            if not os.path.exists (checkout_path):
                printerr ("Error measuring %s@%s. File not found", (checkout_path, rev))
                continue
                
            fm = create_file_metrics (checkout_path)
                    
            # Measure the file
            printdbg ("Measuring %s @ %s", (checkout_path, rev))
            measures = Measures ()

            profiler_start ("[LOC] Measuring %s @ %s", (checkout_path, rev))
            try:
                measures.loc = fm.get_LOC ()
            except Exception, e:
                printerr ('Error running loc for %s@%s. Exception: %s', (checkout_path, rev, str (e)))
            profiler_stop ("[LOC] Measuring %s @ %s", (checkout_path, rev))

            profiler_start ("[SLOC] Measuring %s @ %s", (checkout_path, rev))
            try:
                measures.sloc, measures.lang = fm.get_SLOCLang ()
            except ProgramNotFound, e:
                printout ('Program %s is not installed. Skipping sloc metric', (e.program, ))
            except Exception, e:
                printerr ('Error running sloc for %s@%s. Exception: %s', (checkout_path, rev, str (e)))
            profiler_stop ("[SLOC] Measuring %s @ %s", (checkout_path, rev))

            profiler_start ("[CommentsBlank] Measuring %s @ %s", (checkout_path, rev))
            try:
                measures.ncomment, measures.lcomment, measures.lblank = fm.get_CommentsBlank ()
            except NotImplementedError:
                pass
            except ProgramNotFound, e:
                printout ('Program %s is not installed. Skipping CommentsBlank metric', (e.program, ))
            except Exception, e:
                printerr ('Error running CommentsBlank for %s@%s. Exception: %s', (checkout_path, rev, str (e)))
            profiler_stop ("[CommentsBlank] Measuring %s @ %s", (checkout_path, rev))

            profiler_start ("[HalsteadComplexity] Measuring %s @ %s", (checkout_path, rev))
            try:
                measures.halstead_length, measures.halstead_vol, \
                    measures.halstead_level, measures.halstead_md = fm.get_HalsteadComplexity ()
            except NotImplementedError:
                pass
            except ProgramNotFound, e:
                printout ('Program %s is not installed. Skipping halstead metric', (e.program, ))
            except Exception, e:
                printerr ('Error running cmetrics halstead for %s@%s. Exception: %s', (checkout_path, rev, str (e)))
            profiler_stop ("[HalsteadComplexity] Measuring %s @ %s", (checkout_path, rev))
                
            profiler_start ("[MccabeComplexity] Measuring %s @ %s", (checkout_path, rev))
            try:
                measures.mccabe_sum, measures.mccabe_min, measures.mccabe_max, \
                    measures.mccabe_mean, measures.mccabe_median, \
                    measures.nfunctions = fm.get_MccabeComplexity ()
            except NotImplementedError:
                pass
            except ProgramNotFound, e:
                printout ('Program %s is not installed. Skipping mccabe metric', (e.program, ))
            except Exception, e:
                printerr ('Error running cmetrics mccabe for %s@%s. Exception: %s', (checkout_path, rev, str(e)))
            profiler_stop ("[MccabeComplexity] Measuring %s @ %s", (checkout_path, rev))

            self.metrics.append ((id_counter, file_id, commit_id, measures.lang, measures.sloc, measures.loc,
                                  measures.ncomment, measures.lcomment, measures.lblank, measures.nfunctions,
                                  measures.mccabe_max, measures.mccabe_min, measures.mccabe_sum, measures.mccabe_mean,
                                  measures.mccabe_median, measures.halstead_length, measures.halstead_vol,
                                  measures.halstead_level, measures.halstead_md))

            if len (self.metrics) >= self.MAX_METRICS:
                profiler_start ("Inserting results in db")
                self.__insert_many (write_cursor)
                cnn.commit ()
                profiler_stop ("Inserting results in db")
                    
            id_counter += 1

        profiler_start ("Inserting results in db")
        self.__insert_many (write_cursor)
        cnn.commit ()
        profiler_stop ("Inserting results in db")

        # Clean tmpdir
        # TODO: how long would this take? 
        remove_directory (tmpdir)

        read_cursor.close ()
        write_cursor.close ()
        cnn.close()
        
        profiler_stop ("Running Metrics extension")

register_extension ("Metrics", Metrics)
