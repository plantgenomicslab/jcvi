#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
Finishing pipeline, starting with a phase1/2 BAC. The pipeline ideally should
include the following components

+ BLAST against the Illumina contigs to fish out additional seqs
+ Use minimus2 to combine the contigs through overlaps
+ Map the mates to the contigs and perform scaffolding
+ Base corrections using Illumina reads
"""

import os
import os.path as op
import sys
import logging

from collections import defaultdict
from optparse import OptionParser

from jcvi.formats.agp import build
from jcvi.formats.contig import ContigFile
from jcvi.formats.fasta import Fasta, SeqIO, gaps, format, tidy
from jcvi.formats.sizes import Sizes
from jcvi.utils.cbook import depends
from jcvi.assembly.base import n50
from jcvi.assembly.bundle import LinkLine
from jcvi.apps.command import run_megablast
from jcvi.apps.base import ActionDispatcher, debug, sh, mkdir, is_newer_file
debug()


def main():

    actions = (
        ('overlap', 'build larger contig set by fishing additional seqs'),
        ('overlapbatch', 'call overlap on a set of sequences'),
        ('scaffold', 'build scaffolds from contig links'),
            )
    p = ActionDispatcher(actions)
    p.dispatch(globals())


def scaffold(args):
    """
    %prog scaffold ctgfasta linksfile

    Use the linksfile to build scaffolds. The linksfile can be
    generated by calling assembly.bundle.link() or assembly.bundle.bundle().
    Use --prefix to place the sequences with same prefix together.
    """
    from jcvi.algorithms.graph import nx

    p = OptionParser(scaffold.__doc__)
    p.add_option("--prefix", default=False, action="store_true",
            help="Keep IDs with same prefix together [default: %default]")
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    ctgfasta, linksfile = args
    sizes = Sizes(ctgfasta).mapping
    logfile = "scaffold.log"
    fwlog = open(logfile, "w")

    pref = ctgfasta.rsplit(".", 1)[0]
    agpfile = pref + ".agp"
    fwagp = open(agpfile, "w")
    phasefile = pref + ".phases"
    fwphase = open(phasefile, "w")

    clinks = []
    g = nx.MultiGraph()  # use this to get connected components

    fp = open(linksfile)
    for row in fp:
        c = LinkLine(row)
        distance = max(c.distance, 50)

        g.add_edge(c.aseqid, c.bseqid,
                orientation=c.orientation, distance=distance)

    def get_bname(sname, prefix=False):
        if opts.prefix:
            bname = sname.rsplit("_", 1)[0]
        else:
            bname = "chr0"
        return bname

    scaffoldbuckets = defaultdict(list)
    seqnames = sorted(sizes.keys())

    for h in nx.connected_component_subgraphs(g):
        partialorder = solve_component(h, sizes, fwlog)
        name = partialorder[0][0]
        bname = get_bname(name, prefix=opts.prefix)
        scaffoldbuckets[bname].append(partialorder)

    ctgbuckets = defaultdict(set)
    for name in seqnames:
        bname = get_bname(name, prefix=opts.prefix)
        ctgbuckets[bname].add(name)

    # Now the buckets contain a mixture of singletons and partially resolved
    # scaffolds. Print the scaffolds first then remaining singletons.
    for bname, ctgs in sorted(ctgbuckets.items()):
        scaffolds = scaffoldbuckets[bname]
        scaffolded = set()
        ctgorder = []
        for scaf in scaffolds:
            for node, start, end, orientation in scaf:
                scaffolded.add(node)
                ctgorder.append((node, orientation))
        singletons = sorted(ctgbuckets[bname] - scaffolded)
        nscaffolds = len(scaffolds)
        nsingletons = len(singletons)
        if nsingletons == 1 and nscaffolds == 0:
            phase = 3
        elif nsingletons == 0 and nscaffolds == 1:
            phase = 2
        else:
            phase = 1

        msg = "{0}: Scaffolds={1} Singletons={2} Phase={3}".\
            format(bname, nscaffolds, nsingletons, phase)
        print >> sys.stderr, msg
        print >> fwphase, "\t".join((bname, str(phase)))

        for singleton in singletons:
            ctgorder.append((singleton, "+"))

        build_agp(bname, ctgorder, sizes, fwagp)

    fwagp.close()
    fastafile = "final.fasta"
    build([agpfile, ctgfasta, fastafile])
    tidy([fastafile])


def build_agp(object, ctgorder, sizes, fwagp, gap_length=100):
    object_beg = 1
    part_number = 1
    for i, (component_id, orientation) in enumerate(ctgorder):
        if i:
            component_type = "U"
            gap_type = "fragment"
            linkage = "yes"
            object_end = object_beg + gap_length - 1
            print >> fwagp, "\t".join(str(x) for x in \
                (object, object_beg, object_end, \
                 part_number, component_type, gap_length, \
                 gap_type, linkage, ""))

            object_beg = object_end + 1
            part_number += 1

        component_type = "W"
        component_beg = 1
        component_end = size = sizes[component_id]
        object_end = object_beg + size - 1
        print >> fwagp, "\t".join(str(x) for x in \
            (object, object_beg, object_end, \
             part_number, component_type, component_id,
             component_beg, component_end, orientation))

        object_beg = object_end + 1
        part_number += 1


def solve_component(h, sizes, fwlog):
    """
    Solve the component first by orientations, then by positions.
    """
    from jcvi.algorithms.matrix import determine_signs, determine_positions
    from jcvi.assembly.base import orientationflips

    nodes, edges = h.nodes(), h.edges(data=True)
    nodes = sorted(nodes)
    inodes = dict((x, i) for i, x in enumerate(nodes))

    # Solve signs
    ledges = []
    for a, b, c in edges:
        orientation = c["orientation"]
        orientation = '+' if orientation[0] == orientation[1] else '-'
        a, b = inodes[a], inodes[b]
        if a > b:
            a, b = b, a

        ledges.append((a, b, orientation))

    N = len(nodes)
    print >> fwlog, N, ", ".join(nodes)

    signs = determine_signs(nodes, ledges)
    print >> fwlog, signs

    # Solve positions
    dedges = []
    for a, b, c in edges:
        orientation = c["orientation"]
        distance = c["distance"]
        a, b = inodes[a], inodes[b]
        if a > b:
            a, b = b, a

        ta = '+' if signs[a] > 0 else '-'
        tb = '+' if signs[b] > 0 else '-'
        pair = ta + tb

        if orientationflips[orientation] == pair:
            distance = - distance
        elif orientation != pair:
            continue

        dedges.append((a, b, distance))

    positions = determine_positions(nodes, dedges)
    print >> fwlog, positions

    bed = []
    for node, sign, position in zip(nodes, signs, positions):
        size = sizes[node]
        if sign < 0:
            start = position - size
            end = position
            orientation = "-"
        else:
            start = position
            end = position + size
            orientation = "+"
        bed.append((node, start, end, orientation))

    key = lambda x: x[1]
    offset = key(min(bed, key=key))
    bed.sort(key=key)
    for node, start, end, orientation in bed:
        start -= offset
        end -= offset
        print >> fwlog, "\t".join(str(x) for x in \
                (node, start, end, orientation))

    return bed


@depends
def run_gapsplit(infile=None, outfile=None):
    gaps([infile, "--split"])
    return outfile


def overlapbatch(args):
    """
    %prog overlapbatch ctgfasta poolfasta

    Fish out the sequences in `poolfasta` that overlap with `ctgfasta`.
    Mix and combine using `minimus2`.
    """
    p = OptionParser(overlap.__doc__)
    opts, args = p.parse_args(args)
    if len(args) != 2:
        sys.exit(not p.print_help())

    ctgfasta, poolfasta = args
    f = Fasta(ctgfasta)
    for k, rec in f.iteritems_ordered():
        fastafile = k + ".fasta"
        fw = open(fastafile, "w")
        SeqIO.write([rec], fw, "fasta")
        fw.close()

        overlap([fastafile, poolfasta])


def overlap(args):
    """
    %prog overlap ctgfasta poolfasta

    Fish out the sequences in `poolfasta` that overlap with `ctgfasta`.
    Mix and combine using `minimus2`.
    """
    p = OptionParser(overlap.__doc__)
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    ctgfasta, poolfasta = args
    prefix = ctgfasta.split(".")[0]
    rid = list(Fasta(ctgfasta).iterkeys())
    assert len(rid) == 1, "Use overlapbatch() to improve multi-FASTA file"

    rid = rid[0]
    splitctgfasta = ctgfasta.rsplit(".", 1)[0] + ".split.fasta"
    ctgfasta = run_gapsplit(infile=ctgfasta, outfile=splitctgfasta)

    # Run BLAST
    blastfile = ctgfasta + ".blast"
    run_megablast(infile=ctgfasta, outfile=blastfile, db=poolfasta)

    # Extract contigs and merge using minimus2
    closuredir = prefix + ".closure"
    closure = False
    if not op.exists(closuredir) or is_newer_file(blastfile, closuredir):
        mkdir(closuredir, overwrite=True)
        closure = True

    if closure:
        idsfile = op.join(closuredir, prefix + ".ids")
        cmd = "cut -f2 {0} | sort -u".format(blastfile)
        sh(cmd, outfile=idsfile)

        idsfastafile = op.join(closuredir, prefix + ".ids.fasta")
        cmd = "faSomeRecords {0} {1} {2}".format(poolfasta, idsfile, idsfastafile)
        sh(cmd)

        # This step is a hack to weight the bases from original sequences more
        # than the pulled sequences, by literally adding another copy to be used
        # in consensus calls.
        redundantfastafile = op.join(closuredir, prefix + ".redundant.fasta")
        format([ctgfasta, redundantfastafile, "--prefix=RED."])

        mergedfastafile = op.join(closuredir, prefix + ".merged.fasta")
        cmd = "cat {0} {1} {2}".format(ctgfasta, redundantfastafile, idsfastafile)
        sh(cmd, outfile=mergedfastafile)

        afgfile = op.join(closuredir, prefix + ".afg")
        cmd = "toAmos -s {0} -o {1}".format(mergedfastafile, afgfile)
        sh(cmd)

        cwd = os.getcwd()
        os.chdir(closuredir)
        cmd = "minimus2 {0} -D REFCOUNT=0".format(prefix)
        cmd += " -D OVERLAP=100 -D MINID=98"
        sh(cmd)
        os.chdir(cwd)

    # Analyze output, make sure that:
    # + Get the singletons of the original set back
    # + Drop any contig that is comprised entirely of pulled set
    originalIDs = set(Fasta(ctgfasta).iterkeys())
    minimuscontig = op.join(closuredir, prefix + ".contig")
    c = ContigFile(minimuscontig)
    excludecontigs = set()
    for rec in c.iter_records():
        reads = set(x.id for x in rec.reads)
        if reads.isdisjoint(originalIDs):
            excludecontigs.add(rec.id)

    logging.debug("Exclude contigs: {0}".\
            format(", ".join(sorted(excludecontigs))))

    finalfasta = prefix + ".improved.fasta_"
    fw = open(finalfasta, "w")
    minimusfasta = op.join(closuredir, prefix + ".fasta")
    f = Fasta(minimusfasta)
    for id, rec in f.iteritems_ordered():
        if id in excludecontigs:
            continue
        SeqIO.write([rec], fw, "fasta")

    singletonfile = op.join(closuredir, prefix + ".singletons")
    singletons = set(x.strip() for x in open(singletonfile))
    leftovers = singletons & originalIDs

    logging.debug("Pull leftover singletons: {0}".\
            format(", ".join(sorted(leftovers))))

    f = Fasta(ctgfasta)
    for id, rec in f.iteritems_ordered():
        if id not in leftovers:
            continue
        SeqIO.write([rec], fw, "fasta")

    fw.close()

    fastafile = finalfasta
    finalfasta = fastafile.rstrip("_")
    format([fastafile, finalfasta, "--sequential", "--pad0=3",
        "--prefix={0}_".format(rid)])

    logging.debug("Improved FASTA written to `{0}`.".format(finalfasta))

    n50([ctgfasta])
    n50([finalfasta])

    errlog = "error.log"
    for f in (fastafile, blastfile, errlog):
        if op.exists(f):
            os.remove(f)


if __name__ == '__main__':
    main()
