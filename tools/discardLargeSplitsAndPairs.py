#!/usr/local/python/2.7.9-2/bin/python
# discarLargeSplitsAndPairs.py

import sys
import argparse
import pprint
import re

pp = pprint.PrettyPrinter(indent=4)

### --- read_arguments() ---------------------------------------------------- ###


def read_arguments():
    parser = argparse.ArgumentParser(
        description="Discards split reads that skip more than N nucleotides and read pairs with final template length larger then M")
    parser.add_argument('--version', action='version', version='%(prog)s 1.0')
    parser.add_argument(
        'infile',
        nargs='?',
        type=argparse.FileType('r'),
        default=sys.stdin,
        help="Infile: in .SAM format, default=stdin")
    parser.add_argument(
        'outfile',
        nargs='?',
        type=argparse.FileType('w'),
        default=sys.stdout,
        help="Outfile: in .SAM format, default=stdout")
    parser.add_argument(
        '--logfile',
        nargs='?',
        type=argparse.FileType('w'),
        default=sys.stderr,
        help="Discarded reads in .SAM format, default=stderr")
    parser.add_argument(
        '--statsfile',
        type=argparse.FileType('w'),
        help="File containing the statistics of this procedure in .txt format")
    parser.add_argument(
        '--N_splits',
        type=int,
        help="Size of the skipped region within a split read (in nucleotides). Split Reads that skip more nt than this value are discarded.")
    parser.add_argument(
        '--M_mates',
        type=int,
        help="Size of template (in nucleotides) that would arise from a read pair. Read pairs that exceed this value are discarded.")
    return parser.parse_args()

### --- main() -------------------------------------------------------------- ###


def main(args):

    # dictionary containing all R1 reads that are waiting for their R2 mate
    # if R2 is found, the read pair is processed and deleted from the
    # dictionary
    R1s = {}

    readlinesN = 0
    matesN = 0
    singleN = 0  # number of single reads
    pairedN = 0  # number of read pairs
    splitsSingleN = 0  # number of single reads that are split reads
    # number of paired split reads (R1 or R2 of a pair is a split read)
    splitsPairedN = 0
    splitsSingleNd = 0  # number of discarded single split reads
    splitsPairedNd = 0  # number of discarded paired split reads
    otherN = 0
    discardsN = 0
    discardsM = 0

    for line in args.infile:

        lineBR = line  # line incl. linebreak
        line = line.rstrip('\n')  # line excl. linebreak
        x = line.split()  # line split based on ' '

        # Header lines
        if '@' in x[0]:
            # collect the header lines for the output SAM files
            args.outfile.write(lineBR)
            args.logfile.write(lineBR)
            # don't do anything else with the header lines
            continue

        # Read lines
        readlinesN += 1

        if '*' == x[6]:  # this is a single read
            singleN += 1
            if 'N' in x[5]:  # this is a single read that is a split read
                splitsSingleN += 1
                y = re.search(r'(\d+)N', x[5])
                # if skipped region in range
                if int(y.group(1)) <= args.N_splits:
                    args.outfile.write(lineBR)  # write R it to outfile
                else:  # otherwise
                    args.logfile.write(lineBR)  # log its dismissal
                    splitsSingleNd += 1
            else:  # this is not a split read
                args.outfile.write(lineBR)

        elif '=' == x[6]:  # this read is part of a mate pair
            value1 = int(x[8])  # TLEN for R1
            value2 = int(x[8]) * (-1)  # TLEN for R2
            # uniq identification of a read
            key1 = '%s_%s_%s_%d' % (x[0], x[3], x[7], value1)
            # if this is R2 we need to switch POS and RNEXT and *(-1) TLEN
            key2 = '%s_%s_%s_%d' % (x[0], x[7], x[3], value2)
            if key2 in R1s.keys():  # mate found, process read pair
                pairedN += 1

            else:  # no R1 found, so this is it
                R1s[key1] = lineBR
                # if TLEN too large
                if abs(value1) > args.M_mates:  # log the dismissal of this read pair
                    args.logfile.write(R1s[key2])
                    args.logfile.write(lineBR)
                    splitsPairedNd += 1
                else:  # TLEN is in range
                    if 'N' in x[5]:  # this is a paired read that is a split read
                        splitsPairedN += 1
                    else:  # nope, R2 is not a split read, check R1
                        # split the line from R1 again and check if it is a
                        # split read
                        pass
        else:  # this read is neither single read nor part of mate pair.. just keep it
            otherN += 1
            args.outfile.write(lineBR)

        # old part
        # 1. if split read
        if 'N' in x[5]:
            # is this split read part of a mate pair?
            if '=' == x[6]:  # yes
                pass
            else:  # no
                splitsN += 1
                y = re.search(r'(\d+)N', x[5])
                # if skipped region in range
                if int(y.group(1)) <= args.N_splits:
                    args.outfile.write(lineBR)  # write it to outfile
                else:  # otherwise
                    args.logfile.write(lineBR)  # log its dismissal
                    discardsN += 1

        # 2. if read 1 of mate pair
        elif '=' == x[6]:  # this read is part of a read pair
            value1 = int(x[8])  # TLEN for R1
            value2 = int(x[8]) * (-1)  # TLEN for R2
            # uniq identification of a read
            key1 = '%s_%s_%s_%d' % (x[0], x[3], x[7], value1)
            # if this is R2 we need to switch POS and RNEXT and *(-1) TLEN
            key2 = '%s_%s_%s_%d' % (x[0], x[7], x[3], value2)
            if key2 in R1s.keys():  # mate found, process read pair
                matesN += 1
                # if TLEN is in range
                if abs(value1) <= args.M_mates:  # write read pair to output file
                    args.outfile.write(R1s[key2])
                    args.outfile.write(lineBR)
                else:  # otherwise
                    # log the dismissal of this read pair
                    args.logfile.write(R1s[key2])
                    args.logfile.write(lineBR)
                    discardsM += 1
                del R1s[key2]  # delete R1 from hash
            else:  # no R1 found, so this is it
                R1s[key1] = lineBR

        # 3. all other reads are printed to outfile
        else:
            otherN += 1
            args.outfile.write(lineBR)

    # print the final info into stats file
    args.statsfile.write('# of processed reads:       %10d\n' % (readlinesN))
    if readlinesN > 0:
        args.statsfile.write(
            '# of mapped read pairs:     %10d\t (%5.2f%% of the reads)\n' %
            (matesN, 100.0 / readlinesN * matesN * 2))
        args.statsfile.write(
            '# of split reads:           %10d\t (%5.2f%% of the reads)\n' %
            (splitsN, 100.0 / readlinesN * splitsN))
        args.statsfile.write(
            '# of other reads:           %10d\t (%5.2f%% of the reads)\n' %
            (otherN, 100.0 / readlinesN * otherN))
    else:
        args.statsfile.write(
            '# of mapped read pairs:     %10d\t (%5.2f%% of the reads)\n' %
            (0, 0.0))
        args.statsfile.write(
            '# of split reads:           %10d\t (%5.2f%% of the reads)\n' %
            (0, 0.0))
        args.statsfile.write(
            '# of other reads:           %10d\t (%5.2f%% of the reads)\n' %
            (0, 0.0))

    if splitsN > 0:
        args.statsfile.write(
            '# of discarded split reads: %10d\t (%5.2f%% of the split reads)\n' %
            (discardsN, 100.0 / splitsN * discardsN))
    else:
        args.statsfile.write(
            '# of discarded split reads: %10d\t (%5.2f%% of the split reads)\n' %
            (0, 0.0))
    if matesN > 0:
        args.statsfile.write(
            '# of discarded read pairs:  %10d\t (%5.2f%% of the read pairs)\n' %
            (discardsM, 100.0 / matesN * discardsM))
    else:
        args.statsfile.write(
            '# of discarded read pairs:  %10d\t (%5.2f%% of the read pairs)\n' %
            (0, 0.0))


if __name__ == '__main__':
    args = read_arguments()
    main(args)

__version__ = '1.0'
