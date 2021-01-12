# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
import os
from datetime import datetime

testDict = {}

testFile = open('tests')

prefix = 'Logos.'

authors = {
'congjian.wang': 'wangc',
'diego.mandelli': 'mandd',
}

while True:
  line = testFile.readline()
  if not line:
    break
  line = line.strip()
  if line.startswith('[./'):
    token = line.replace('[./','').replace(']','')
    nextLine = testFile.readline().strip()
    while not nextLine.startswith('input'):
      nextLine = testFile.readline()
      if not nextLine:
        break
      nextLine = nextLine.strip()
    if not nextLine:
      break
    inputFile = nextLine.split(' = ')[1].replace('\'', '')
    testDict[inputFile] = prefix + token

testFile.close()

for key,value in testDict.items():
  print(key,value)
  tokens = key.rsplit('.',1)[0].split('_')
  if 'smooth' in tokens:
    smooth = True
    kernel = tokens[2]
  else:
    smooth = False
    try: kernel = tokens[1]
                except: kernel = "None"
  text = os.popen('git log %s' % key).read()

  revisions = []
  lines = text.split('\n')
  i = 0
  while i < len(lines):
    line = lines[i]
    if line.startswith('Author:'):
      author = line.rsplit('<',1)[1].rsplit('@',1)[0].lower()
      if author in authors:
        author = authors[author]
      i += 1
      line = lines[i]
      date = line.split(':',1)[1][:-6].strip()
      date = datetime.strptime(date, '%a %b %d %H:%M:%S %Y').strftime('%Y-%m-%d')
      i += 2
      line = lines[i]
      description = ''
      while not line.startswith('commit'):
        description += line
        i += 1
        if i >= len(lines):
          break
        line = lines[i]
      revisions.append((author,date,description))
    i += 1

  try: author,date,description = revisions.pop()
        except: pass
  revisions.reverse()
  inputFile = open(key.split()[0],'r')
  lines = [line for line in inputFile]
  inputFile.close()
        foundTestDescription = False
        for line in lines:
            if 'TestInfo' in line:
                foundTestDescription = True
                break
        if not foundTestDescription:
            for i,line in enumerate(lines):
                    if 'riam' in line:
                            break

            newLines = []
            if key.split()[0].strip().endswith("py"): newLines.append('  """\n')
            newLines.append('  <TestInfo>\n')
            newLines.append('    <name>%s</name>\n' % value)
            newLines.append('    <author>%s</author>\n' % author)
            newLines.append('    <created>%s</created>\n' % date)
            newLines.append('    <classesTested> </classesTested>\n')
            newLines.append('    <description>\n')
            newLines.append('       This test is aimed to check .\n')
            newLines.append('    </description>\n')
            newLines.append('    <revisions>\n')
            if len(revisions)>0:
                for author,date,description in revisions:
                        newLines.append('      <revision author="%s" date="%s">%s</revision>\n' % (author,date,description.strip()))
                newLines.append('      <revision author="alfoa" date="2017-01-21">Adding this test description.</revision>\n')
                newLines.append('    </revisions>\n')
            newLines.append('  </TestInfo>\n')
            if key.split()[0].strip().endswith("py"): newLines.append('  """\n')
            lines = lines[:(i+1)] + newLines + lines[(i+1):]
            lines.replace('riam','Logos')

            inputFile = open(key,'w')
            inputFile.write(''.join(lines))
            inputFile.close()
