"""
Commonly performed commands.
"""

import os
import os.path as op
import sys
import shutil
import logging
import ConfigParser

from functools import partial
from optparse import OptionParser

from jcvi.utils.cbook import depends

from jcvi.apps.base import ActionDispatcher, debug, sh, is_exe, which
debug()


def getpath(cmd, name=None, url=None, cfg="~/.jcvirc"):
    """
    Get install locations of common binaries
    First, check ~/.jcvirc file to get the full path
    If not present, ask on the console and and store
    """
    p = which(cmd)  # if in PATH, just returns it
    if p:
        return p

    PATH = "Path"
    config = ConfigParser.RawConfigParser()
    cfg = op.expanduser(cfg)
    changed = False
    if op.exists(cfg):
        config.read(cfg)

    assert name is not None, "Need a program name"

    try:
        fullpath = config.get(PATH, name)
    except ConfigParser.NoSectionError:
        config.add_section(PATH)
        changed = True
    except:
        pass

    try:
        fullpath = config.get(PATH, name)
    except ConfigParser.NoOptionError:
        msg = "=== Configure path for {0} ===\n".format(name, cfg)
        if url:
            msg += "URL: <{0}>\n".format(url)
        msg += "[Directory that contains `{0}`]: ".format(cmd)
        fullpath = raw_input(msg).strip()
        config.set(PATH, name, fullpath)
        changed = True

    path = op.join(fullpath, cmd)
    assert is_exe(path), \
            "Cannot execute binary `{0}`. Please verify and rerun.".format(path)

    if changed:
        configfile = open(cfg, "w")
        config.write(configfile)
        logging.debug("Configuration written to `{0}`.".format(cfg))

    return path


BLPATH = partial(getpath, name="BLAST", url=\
        "ftp://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/LATEST/")
BDPATH = partial(getpath, name="BEDTOOLS", url=\
        "http://code.google.com/p/bedtools/")
CAPATH = partial(getpath, name="Celera-Assembler", url=\
        "http://wgs-assembler.sf.net")
CDPATH = partial(getpath, name="CD-HIT", url=\
        "http://weizhong-lab.ucsd.edu/cd-hit/")
EMBOSSPATH = partial(getpath, name="EMBOSS", url=\
        "http://emboss.sourceforge.net/")
JAVAPATH = partial(getpath, name="Java1.6", url=\
        "http://www.java.com/")


@depends
def run_formatdb(infile=None, outfile=None):
    cmd = BLPATH("makeblastdb")
    cmd += " -dbtype nucl -in {0}".format(infile)
    sh(cmd)


@depends
def run_blat(infile=None, outfile=None, db="UniVec_Core", pctid=95, hitlen=50):
    cmd = 'blat {0} {1} -out=blast8 {2}'.format(db, infile, outfile)
    sh(cmd)

    blatfile = outfile
    filtered_blatfile = outfile + ".P{0}L{1}".format(pctid, hitlen)
    run_blast_filter(infile=blatfile, outfile=filtered_blatfile,
            pctid=pctid, hitlen=hitlen)
    shutil.move(filtered_blatfile, blatfile)


@depends
def run_vecscreen(infile=None, outfile=None, db="UniVec_Core",
        pctid=None, hitlen=None):
    """
    BLASTN parameters reference:
    http://www.ncbi.nlm.nih.gov/VecScreen/VecScreen_docs.html
    """
    nin = db + ".nin"
    run_formatdb(infile=db, outfile=nin)

    cmd = BLPATH("blastn")
    cmd += " -task blastn"
    cmd += " -query {0} -db {1} -out {2}".format(infile, db, outfile)
    cmd += " -penalty -5 -gapopen 4 -gapextend 4 -dust yes -soft_masking true"
    cmd += " -searchsp 1750000000000 -evalue 0.01 -outfmt 6 -num_threads 8"
    sh(cmd)


@depends
def run_megablast(infile=None, outfile=None, db=None, pctid=98, hitlen=100):
    assert db, "Need to specify database fasta file."

    nin = db + ".nin"
    run_formatdb(infile=db, outfile=nin)

    cmd = BLPATH("blastn")
    cmd += " -query {0} -db {1} -out {2}".format(infile, db, outfile)
    cmd += " -evalue 0.01 -outfmt 6 -num_threads 16"
    sh(cmd)

    blastfile = outfile
    filtered_blastfile = outfile + ".P{0}L{1}".format(pctid, hitlen)
    run_blast_filter(infile=blastfile, outfile=filtered_blastfile,
            pctid=pctid, hitlen=hitlen)
    shutil.move(filtered_blastfile, blastfile)


def run_blast_filter(infile=None, outfile=None, pctid=95, hitlen=50):
    from jcvi.formats.blast import filter

    logging.debug("Filter BLAST result (pctid={0}, hitlen={1})".\
            format(pctid, hitlen))
    pctidopt = "--pctid={0}".format(pctid)
    hitlenopt = "--hitlen={0}".format(hitlen)
    filter([infile, pctidopt, hitlenopt])


def main():

    actions = (
        ('megablast', 'run megablast using query against reference'),
            )
    p = ActionDispatcher(actions)
    p.dispatch(globals())


def megablast(args):
    """
    %prog megablast ref.fasta query.fasta

    Calls megablast and then filter the BLAST hits.
    """
    p = OptionParser(megablast.__doc__)
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    reffasta, queryfasta = args
    q = queryfasta.split(".")[0]
    r = reffasta.split(".")[0]
    blastfile = "{0}.{1}.blast".format(q, r)

    run_megablast(infile=queryfasta, outfile=blastfile, db=reffasta)


if __name__ == '__main__':
    main()
