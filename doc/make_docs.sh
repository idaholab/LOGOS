#!/bin/bash
SCRIPT_NAME=`readlink $0`
if test -x "$SCRIPT_NAME";
then
    SCRIPT_DIRNAME=`dirname $SCRIPT_NAME`
else
    SCRIPT_DIRNAME=`dirname $0`
fi
SCRIPT_DIR=`(cd $SCRIPT_DIRNAME; pwd)`

set -o errexit

#building user manual
cd $SCRIPT_DIR/user_manual/
make

#sqa manual
cd $SCRIPT_DIR/sqa/
./make_docs.sh
