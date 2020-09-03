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

import argparse
import os
import re
import subprocess
import sys
import textwrap
from collections import namedtuple

RE_SOURCE_FILENAME = re.compile(r'^--- (?P<filename>[^\t]+)')
RE_TARGET_FILENAME = re.compile(r'^\+\+\+ (?P<filename>[^\t]+)')
RE_HUNK_HEADER = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))?\ @@')

ChangedFile = namedtuple('ChangedFile', ['source', 'target', 'hunks'])
Hunk = namedtuple('Hunk', ['start1', 'len1', 'start2', 'len2'])


def colored(s, color):
    if os.isatty(sys.stdout.fileno()):
        return '\033[3%im%s\033[39m' % (color, s)
    else:
        return s


def git(*args):
    output = subprocess.check_output(['git', *args])
    return str(output, encoding='utf-8', errors='ignore').strip().split('\n')


def parse_diff(lines):
    ret = []
    for line in lines:
        check_source = RE_SOURCE_FILENAME.match(line)
        if check_source:
            source_file = check_source.group('filename')
            if source_file.startswith('a/'):
                source_file = source_file[2:]
            continue

        check_target = RE_TARGET_FILENAME.match(line)
        if check_target:
            target_file = check_target.group('filename')
            if target_file.startswith('b/'):
                target_file = target_file[2:]
            ret.append(ChangedFile(source_file, target_file, []))
            continue

        re_hunk_header = RE_HUNK_HEADER.match(line)
        if re_hunk_header:
            ret[-1].hunks.append(Hunk(*[
                1 if i is None else int(i, 10)
                for i in re_hunk_header.groups()
            ]))
    return ret


class Change:
    def __init__(self, filename, start, length):
        self.filename = filename
        self.start = start
        self.len = length


class Commit:
    def __init__(self, rev, msg):
        self.rev = rev
        self.msg = msg
        self.changes = []
        self.deps = set()

    def __str__(self):
        return '%s %s' % (colored(self.rev, 3), self.msg)


class History:
    def __init__(self):
        self.commits = []

    def apply_hunk(self, filename, hunk):
        deps = set()
        for commit in self.commits:
            for change in commit.changes:
                if change.filename == filename:
                    # TODO: probably can do more here
                    # these are only heuristics anyway
                    if change.start > hunk.start1:
                        change.start += hunk.len2 - hunk.len1
                    elif change.start + change.len >= hunk.start1 + hunk.len1:
                        change.len += hunk.len2 - hunk.len1

                    if (
                        change.start < hunk.start2 + hunk.len2
                        and hunk.start2 < change.start + change.len
                    ):
                        deps.add(commit.rev)
        return deps

    def push_commit(self, rev, msg, diff):
        new_commit = Commit(rev, msg)

        for changed_file in diff:
            for hunk in changed_file.hunks:
                deps = self.apply_hunk(changed_file.source, hunk)
                new_commit.deps.update(deps)
                new_commit.changes.append(
                    Change(changed_file.target, hunk.start2, hunk.len2)
                )

            for commit in self.commits:
                for change in commit.changes:
                    if change.filename == changed_file.source:
                        change.filename = changed_file.target
                        if changed_file.target == '/dev/null':
                            new_commit.deps.add(commit.rev)

        self.commits.append(new_commit)

    @classmethod
    def from_git(cls, *args, context=2):
        history = History()
        lines = git('rev-list', '--oneline', '--reverse', *args)
        for line in lines:
            rev, msg = line.split(' ', 1)
            diff = git('show', '-U%i' % context, rev)
            history.push_commit(rev, msg, parse_diff(diff))
        return history


def print_depends(history):
    for commit in history.commits:
        print(commit)
        for dep in history.commits:
            if dep.rev in commit.deps:
                print('  %s' % dep)


def print_matrix(history):
    max_len = max(len(str(c)) for c in history.commits)
    for i, commit in enumerate(history.commits):
        line = str(commit)
        if commit.deps:
            line += ' ' + '-' * (max_len - len(line) + i * 2) + "' "
        else:
            line += ' ' + ' ' * (max_len - len(line) + i * 2) + '  '
        for later in history.commits[i + 1:]:
            if commit.rev in later.deps:
                line += 'X '
            elif later.deps.difference([c.rev for c in history.commits[i:]]):
                line += '| '
            else:
                line += '  '
        print(line)


def print_dot(history):
    print('digraph CommitDependencies  {')
    for commit in history.commits:
        label = str(commit).replace('\\', '\\\\').replace('"', '\\"')
        label = '\\n'.join(textwrap.wrap(label, 25))
        print('"%s" [label="%s"]' % (commit.rev, label))
        for dep in commit.deps:
            print('"%s" -> "%s"' % (dep, commit.rev))
    print('}')


def main():
    parser = argparse.ArgumentParser(
        description='Find dependencies among git commits.'
    )
    parser.add_argument(
        'arguments',
        metavar='ARG',
        nargs='*',
        default=['HEAD'],
        help=(
            'Specification of commits to analyze. This is '
            'passed to git rev-list as-is (so use a valid '
            'revision range, like HEAD^^..HEAD).'
        ),
    )
    parser.add_argument(
        '--context',
        '-C',
        default='2',
        metavar='LINES',
        type=int,
        help=(
            'The number of lines around a change that are '
            'condiered part of that change. Pass 0 to '
            'only consider the changed lines themselves.'
        ),
    )
    parser.add_argument(
        '--output',
        '-o',
        default='matrix',
        choices=['list', 'matrix', 'dot'],
        help='Output format',
    )
    args = parser.parse_args()

    history = History.from_git(*args.arguments, context=args.context)
    if args.output == 'list':
        print_depends(history)
    elif args.output == 'matrix':
        print_matrix(history)
    elif args.output == 'dot':
        print_dot(history)


if __name__ == '__main__':
    main()
