#!/bin/bash
PYTHON_CMD=${PYTHON_CMD:=python}
SCRIPT_DIRNAME=`dirname $0`
SCRIPT_DIR=`(cd $SCRIPT_DIRNAME; pwd)`

$PYTHON_CMD ${SCRIPT_DIR}/validate_xml.py
