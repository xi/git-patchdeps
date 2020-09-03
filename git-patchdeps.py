#!/usr/bin/env python3

# Copyright (c) 2012 Matias Bordese
# Copyright (c) 2013 Matthijs Kooijman <matthijs@stdin.nl>
# Copyright (c) 2014 Tobias Bengfort <tobias.bengfort@gmx.net>
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
# Simple script to process a list of patch files and identify obvious
# dependencies between them. Inspired by the similar (but more limited)
# perl script published at
# http://blog.mozilla.org/sfink/2012/01/05/patch-queue-dependencies/

"""Unified diff parser module."""

# This file is based on the unidiff library by Matías Bordese (at
# https://github.com/matiasb/python-unidiff)

import argparse
import collections
import itertools
import os
import re
import subprocess
import sys
import textwrap

RE_SOURCE_FILENAME = re.compile(r'^--- (?P<filename>[^\t]+)')
RE_TARGET_FILENAME = re.compile(r'^\+\+\+ (?P<filename>[^\t]+)')

# @@ (source offset, length) (target offset, length) @@
RE_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))?\ @@")

#   kept line (context)
# + added line
# - deleted line
# \ No newline case (ignore)
RE_HUNK_BODY_LINE = re.compile(r'^([- \+\\])')

LINE_TYPE_ADD = '+'
LINE_TYPE_DELETE = '-'
LINE_TYPE_CONTEXT = ' '


class UnidiffParseException(Exception):
    pass


class Change:
    """A single line from a patch hunk."""

    def __init__(self, hunk, action, source_lineno_rel, source_line,
                 target_lineno_rel, target_line):
        # The line numbers must always be present, either source_line or
        # target_line can be None depending on the action.
        self.hunk = hunk
        self.action = action
        self.source_lineno_rel = source_lineno_rel
        self.source_line = source_line
        self.target_lineno_rel = target_lineno_rel
        self.target_line = target_line

        self.source_lineno_abs = self.hunk.source_start + self.source_lineno_rel
        self.target_lineno_abs = self.hunk.target_start + self.target_lineno_rel

    def __str__(self):
        return "(-%s, +%s) %s%s" % (self.source_lineno_abs,
                                    self.target_lineno_abs,
                                    self.action,
                                    self.source_line or self.target_line)


class PatchedFile(list):
    """Data from a patched file."""

    def __init__(self, source='', target=''):
        self.source_file = source
        self.target_file = target

        if self.source_file.startswith('a/') and self.target_file.startswith('b/'):
            self.path = self.source_file[2:]
        elif self.source_file.startswith('a/') and self.target_file == '/dev/null':
            self.path = self.source_file[2:]
        elif self.target_file.startswith('b/') and self.source_file == '/dev/null':
            self.path = self.target_file[2:]
        else:
            self.path = self.source_file


class Hunk:
    """Each of the modified blocks of a file."""

    def __init__(self, src_start=0, src_len=0, tgt_start=0, tgt_len=0):
        self.source_start = int(src_start)
        self.source_length = int(src_len)
        self.target_start = int(tgt_start)
        self.target_length = int(tgt_len)
        self.changes = []
        self.to_parse = [self.source_length, self.target_length]

    def is_valid(self):
        """Check hunk header data matches entered lines info."""
        return self.to_parse == [0, 0]

    def append_change(self, change):
        """Append a Change."""
        self.changes.append(change)

        if (change.action == LINE_TYPE_CONTEXT or
            change.action == LINE_TYPE_DELETE):
                self.to_parse[0] -= 1
                if self.to_parse[0] < 0:
                    raise UnidiffParseException(
                        'To many source lines in hunk: %s' % self)

        if (change.action == LINE_TYPE_CONTEXT or
            change.action == LINE_TYPE_ADD):
                self.to_parse[1] -= 1
                if self.to_parse[1] < 0:
                    raise UnidiffParseException(
                        'To many target lines in hunk: %s' % self)

    def __str__(self):
        return "<@@ %d,%d %d,%d @@>" % (self.source_start, self.source_length,
                                        self.target_start, self.target_length)


def _parse_hunk(diff, source_start, source_len, target_start, target_len):
    hunk = Hunk(source_start, source_len, target_start, target_len)
    source_lineno = 0
    target_lineno = 0

    for line in diff:
        valid_line = RE_HUNK_BODY_LINE.match(line)
        if valid_line:
            action = valid_line.group(0)
            original_line = line[1:]

            kwargs = dict(action=action,
                          hunk=hunk,
                          source_lineno_rel=source_lineno,
                          target_lineno_rel=target_lineno,
                          source_line=None,
                          target_line=None)

            if action == LINE_TYPE_ADD:
                kwargs['target_line'] = original_line
                target_lineno += 1
            elif action == LINE_TYPE_DELETE:
                kwargs['source_line'] = original_line
                source_lineno += 1
            elif action == LINE_TYPE_CONTEXT:
                kwargs['source_line'] = original_line
                kwargs['target_line'] = original_line
                source_lineno += 1
                target_lineno += 1
            hunk.append_change(Change(**kwargs))
        else:
            raise UnidiffParseException('Hunk diff data expected: ' + line)

        # check hunk len(old_lines) and len(new_lines) are ok
        if hunk.is_valid():
            break

    return hunk


def parse_diff(diff):
    ret = []
    current_file = None
    # Make sure we only iterate the diff once, instead of restarting
    # from the top inside _parse_hunk
    diff = itertools.chain(diff)

    for line in diff:
        # check for source file header
        check_source = RE_SOURCE_FILENAME.match(line)
        if check_source:
            source_file = check_source.group('filename')
            current_file = None
            continue

        # check for target file header
        check_target = RE_TARGET_FILENAME.match(line)
        if check_target:
            target_file = check_target.group('filename')
            current_file = PatchedFile(source_file, target_file)
            ret.append(current_file)
            continue

        # check for hunk header
        re_hunk_header = RE_HUNK_HEADER.match(line)
        if re_hunk_header:
            hunk_info = list(re_hunk_header.groups())
            # If the hunk length is 1, it is sometimes left out
            for i in (1, 3):
                if hunk_info[i] is None:
                    hunk_info[i] = 1
            hunk = _parse_hunk(diff, *hunk_info)
            current_file.append(hunk)
    return ret


class Bunch:
    def __init__(self, **kwds):
        self.__dict__.update(kwds)


class GitRev:
    def __init__(self, rev, msg):
        self.rev = rev
        self.msg = msg

    def get_diff(self):
        diff = subprocess.check_output(['git', 'show', self.rev])
        # Convert to utf8 and just drop any invalid characters (we're
        # not interested in the actual file contents and all diff
        # special characters are valid ascii).
        return str(diff, encoding='utf-8', errors='ignore').split('\n')

    def get_patch_set(self):
        """Return this changeset as a list of PatchedFiles."""
        return parse_diff(self.get_diff())

    def __str__(self):
        return "%s (%s)" % (self.rev, self.msg)

    @staticmethod
    def get_changesets(args):
        """Generate Changeset objects, given arguments for git rev-list."""
        output = subprocess.check_output(['git', 'rev-list', '--oneline', '--reverse'] + args)

        if not output:
            sys.stderr.write("No revisions specified?\n")
        else:
            lines = str(output, encoding='ascii').strip().split('\n')

            for line in lines:
                yield GitRev(*line.split(' ', 1))


def print_depends(patches, depends):
    for p in patches:
        if not depends[p]:
            continue
        print("%s depends on: " % p)
        for dep in patches:
            if dep in depends[p]:
                desc = getattr(depends[p][dep], 'desc', None)
                if desc:
                    print("  %s (%s)" % (dep, desc))
                else:
                    print("  %s" % dep)


def print_depends_matrix(patches, depends):
    # Which patches have at least one dependency drawn (and thus
    # need lines from then on)?
    has_deps = set()
    for p in patches:
        line = str(p)[:80] + "  "
        if p in has_deps:
            line += "-" * (84 - len(line) + p.number * 2)
            line += "' "
        else:
            line += " " * (84 - len(line) + p.number * 2)
            line += "  "

        for dep in patches[p.number + 1:]:
            # For every later patch, print an "X" if it depends on this
            # one
            if p in depends[dep]:
                line += getattr(depends[dep][p], 'matrixmark', 'X')
                has_deps.add(dep)
            elif dep in has_deps:
                line += "|"
            else:
                line += " "
            line += " "

        print(line)


def dot_escape_string(s):
    return s.replace("\\", "\\\\").replace("\"", "\\\"")


def depends_dot(args, patches, depends):
    """Return dot code for the dependency graph."""
    # Seems that fdp gives the best clustering if patches are often
    # independent
    res = """
digraph ConflictMap {
node [shape=box]
layout=neato
overlap=scale
"""

    if args.randomize:
        res += "start=random\n"

    for p in patches:
        label = dot_escape_string(str(p))
        label = "\\n".join(textwrap.wrap(label, 25))
        res += """{} [label="{}"]\n""".format(p.number, label)
        for dep, v in depends[p].items():
            style = getattr(v, 'dotstyle', 'solid')
            res += """{} -> {} [style={}]\n""".format(dep.number, p.number, style)
    res += "}\n"

    return res


class ByFileAnalyzer:
    def analyze(self, args, patches):
        """
        Find dependencies in a list of patches by looking at the files they change.

        The algorithm is simple: Just keep a list of files changed, and mark
        two patches as conflicting when they change the same file.
        """
        # Which patches touch a particular file. A dict of filename => list
        # of patches
        touches_file = collections.defaultdict(list)

        # Which patch depends on which other patches? A dict of
        # patch => (list of dependency patches)
        depends = collections.defaultdict(dict)

        for patch in patches:
            for f in patch.get_patch_set():
                for other in touches_file[f.path]:
                    depends[patch][other] = True

                touches_file[f.path].append(patch)

        return depends


class ByLineAnalyzer:
    def analyze(self, args, patches):
        """Find dependencies in a list of patches by looking at the lines they change."""
        # Per-file info on which patch last touched a particular line.
        # A dict of file => list of LineState objects
        state = dict()

        # Which patch depends on which other patches?
        # A dict of patch => (dict of patch depended on => type) Here,
        # type is either DEPEND_HARD or DEPEND_PROXIMITY.
        depends = collections.defaultdict(dict)

        for patch in patches:
            for f in patch.get_patch_set():
                if f.path not in state:
                    state[f.path] = ByLineFileAnalyzer(f.path, args.proximity)

                state[f.path].analyze(depends, patch, f)

        return depends


class ByLineFileAnalyzer:
    """
    Helper class for the ByLineAnalyzer, that performs the analysis for
    a specific file. Created once and called for multiple patches.
    """

    # Used if a patch changes a line changed by another patch
    DEPEND_HARD = Bunch(desc='hard', matrixmark='X', dotstyle='solid')
    # Used if a patch changes a line changed near a line changed by
    # another patch
    DEPEND_PROXIMITY = Bunch(desc='proximity', matrixmark='*', dotstyle='dashed')

    def __init__(self, fname, proximity):
        self.fname = fname
        self.proximity = proximity
        # Keep two view on our line state, so we can both iterate them
        # in order and do quick lookups
        self.line_list = []
        self.line_dict = {}

    def analyze(self, depends, patch, hunks):
        # This is the index in line_list of the first line state that
        # still uses source line numbers
        self.to_update_idx = 0

        # The index in line_list of the last line processed (i.e,
        # matched against a diff line)
        self.processed_idx = -1

        # Offset between source and target files at state_pos
        self.offset = 0

        for hunk in hunks:
            self.analyze_hunk(depends, patch, hunk)

        # Pretend we processed the entire list, so update_offset can
        # update the line numbers of any remaining (unchanged) lines
        # after the last hunk in this patch
        self.processed_idx = len(self.line_list)
        self.update_offset(0)

    def line_state(self, lineno, create):
        """
        Return the state of the given (source) line number, creating a
        new empty state if it is not yet present and create is True.
        """
        self.processed_idx += 1
        for state in self.line_list[self.processed_idx:]:
            # Found it, return
            if state.lineno == lineno:
                return state
            elif state.lineno < lineno:
                # We're already passed this one, continue looking
                self.processed_idx += 1
                continue
            else:
                # It's not in there, stop looking
                break
                enumerate

        if not create:
            return None

        # We don't have state for this particular line, insert a
        # new empty state
        state = self.LineState(lineno=lineno)
        self.line_list.insert(self.processed_idx, state)
        return state

    def update_offset(self, amount):
        """
        Update the offset between target and source lines by the
        specified amount.

        Takes care of updating the line states of all processed lines
        (up to but excluding self.processed_idx) with the old offset
        before changing it.
        """
        for state in self.line_list[self.to_update_idx:self.processed_idx]:
            state.lineno += self.offset
            self.to_update_idx += 1

        self.offset += amount

    def analyze_hunk(self, depends, patch, hunk):
        for change in hunk.changes:
            # When adding a line, don't bother creating a new line
            # state, since we'll be adding one anyway (this prevents
            # extra unused linestates)
            create = (change.action != LINE_TYPE_ADD)
            line_state = self.line_state(change.source_lineno_abs, create)

            # When changing a line, claim proximity lines before it as
            # well.
            if change.action != LINE_TYPE_CONTEXT and self.proximity != 0:
                # i points to the only linestate that could contain the
                # state for lineno
                i = self.processed_idx - 1
                lineno = change.source_lineno_abs - 1
                while (change.source_lineno_abs - lineno <= self.proximity and
                       lineno > 0):
                    if (i < 0 or
                        i >= self.to_update_idx and
                        self.line_list[i].lineno < lineno or
                        i < self.to_update_idx and
                        self.line_list[i].lineno - self.offset < lineno):
                            # This line does not exist yet, i points to an
                            # earlier line. Insert it
                            # _after_ i.
                            self.line_list.insert(i + 1, self.LineState(lineno))
                            # Point i at the inserted line
                            i += 1
                            self.processed_idx += 1
                            assert i >= self.to_update_idx, "Inserting before already updated line"

                    # Claim this line
                    s = self.line_list[i]

                    # Already claimed, stop looking. This should also
                    # prevent us from i becoming < to_update_idx - 1,
                    # since the state at to_update_idx - 1 should always
                    # be claimed
                    if patch.number in s.proximity or s.changed_by == patch:
                        break

                    s.proximity[patch.number] = patch
                    i -= 1
                    lineno -= 1

            # For changes that know about the contents of the old line,
            # check if it matches our observations
            if change.action != LINE_TYPE_ADD:
                if (line_state.line is not None and
                    change.source_line != line_state.line):
                        sys.stderr.write("While processing %s\n" % patch)
                        sys.stderr.write("Warning: patch does not apply cleanly! Results are probably wrong!\n")
                        sys.stderr.write("According to previous patches, line %s is:\n" % change.source_lineno_abs)
                        sys.stderr.write("%s\n" % line_state.line)
                        sys.stderr.write("But according to %s, it should be:\n" % patch)
                        sys.stderr.write("%s\n\n" % change.source_line)
                        sys.exit(1)

            if change.action == LINE_TYPE_CONTEXT:
                if line_state.line is None:
                    line_state.line = change.target_line

            elif change.action == LINE_TYPE_ADD:
                self.update_offset(1)

                # Mark this line as changed by this patch
                s = self.LineState(lineno=change.target_lineno_abs,
                                   line=change.target_line,
                                   changed_by=patch)
                self.line_list.insert(self.processed_idx, s)
                assert self.processed_idx == self.to_update_idx, "Not everything updated?"

                # Since we insert this using the target line number, it
                # doesn't need to be updated again
                self.to_update_idx += 1

                # Add proximity deps for patches that touched code
                # around this line. We can't get a hard dependency for
                # an 'add' change, since we don't actually touch any
                # existing code
                if line_state:
                    deps = itertools.chain(line_state.proximity.values(),
                                           [line_state.changed_by])
                    for p in deps:
                        if p and p not in depends[patch] and p != patch:
                            depends[patch][p] = self.DEPEND_PROXIMITY

            elif change.action == LINE_TYPE_DELETE:
                self.update_offset(-1)

                # This file was touched by another patch, add
                # dependency
                if line_state.changed_by:
                    depends[patch][line_state.changed_by] = self.DEPEND_HARD
                    depends[patch][line_state.changed_by].dottooltip = "-" + change.source_line

                # Also add proximity deps for patches that touched code
                # around this line
                for p in line_state.proximity.values():
                    if (p not in depends[patch]) and p != patch:
                        depends[patch][p] = self.DEPEND_PROXIMITY

                # Forget about the state for this source line
                del self.line_list[self.processed_idx]
                self.processed_idx -= 1

            # After changing a line, claim proximity lines after it as
            # well.
            if change.action != LINE_TYPE_CONTEXT and self.proximity != 0:
                # i points to the only linestate that could contain the
                # state for lineno
                i = self.to_update_idx
                lineno = change.source_lineno_abs
                if lineno == 0:  # When a file is created, the source line for the adds is 0...
                    lineno += 1
                while (lineno - change.source_lineno_abs < self.proximity):
                    if (i >= len(self.line_list) or
                        self.line_list[i].lineno > lineno):
                            # This line does not exist yet, i points to an
                            # later line. Insert it _before_ i.
                            self.line_list.insert(i, self.LineState(lineno))
                            assert i > self.processed_idx, "Inserting before already processed line"

                    # Claim this line
                    self.line_list[i].proximity[patch.number] = patch

                    i += 1
                    lineno += 1

    class LineState:
        """State of a particular line in a file."""

        def __init__(self, lineno, line=None, changed_by=None):
            self.lineno = lineno
            self.line = line
            self.changed_by = changed_by
            # Dict of patch number => patch for patches that changed
            # lines near this one
            self.proximity = {}

        def __str__(self):
            return "%s: changed by %s: %s" % (self.lineno, self.changed_by, self.line)


def main():
    parser = argparse.ArgumentParser(description='Analyze patches for dependencies.')
    parser.add_argument('arguments', metavar="ARG", nargs='+', help="""
                        Specification of patches to analyze. This is
                        passed to git rev-list as-is (so use a valid
                        revision range, like HEAD^^..HEAD).""")
    parser.add_argument('--proximity', default='2', metavar='LINES',
                        type=int, help="""
                        The number of lines changes should be apart to
                        prevent being marked as a dependency. Pass 0 to
                        only consider exactly the same line. This option
                        is no used when --by-file is passed. The default
                        value is %(default)s.""")
    parser.add_argument('--randomize', action='store_true', help="""
                        Randomize the graph layout produced by
                        --depends-dot and --depends-xdot.""")
    parser.add_argument('--output', '-o', default='matrix',
                        choices=['list', 'matrix', 'dot'],
                        help="""Output format""")

    args = parser.parse_args()

    patches = list(GitRev.get_changesets(args.arguments))

    for i, p in enumerate(patches):
        p.number = i

    depends = args.analyzer().analyze(args, patches)

    if args.output == 'list':
        print_depends(patches, depends)

    elif args.output == 'matrix':
        print_depends_matrix(patches, depends)

    elif args.output == 'dot':
        print(depends_dot(args, patches, depends))


if __name__ == "__main__":
    main()
