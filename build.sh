# script to install required libraries

# fail if ANYTHING this script fails (mostly, there are exceptions)
set -e

function establish_OS ()
{
	case $OSTYPE in
		"linux")
			OSOPTION="--linux"
			;;
		"linux-gnu")
			OSOPTION="--linux"
			;;
		"darwin"*)
			OSOPTION="--mac"
			;;
		"msys"*)
			OSOPTION="--windows"
			;;
		"cygwin"*)
			OSOPTION="--windows"
			;;
		*)
			echo Unknown OS: $OSTYPE\; ignoring.
			OSOPTION=""
			;;
	esac
}

function find_conda_defs ()
{
	echo ... Conda definitions not defined by --conda-defs option.
  echo ... Search the default place for conda definitions
  # default location of conda definitions, windows is unsurprisingly an exception
  if [[ "$OSOPTION" = "--windows" ]];
  then
    CONDA_DEFS="/c/ProgramData/Miniconda3/etc/profile.d/conda.sh";
  elif test -e "$HOME/miniconda3/etc/profile.d/conda.sh";
  then
    CONDA_DEFS="$HOME/miniconda3/etc/profile.d/conda.sh";
  else
    CONDA_DEFS="$HOME/miniconda2/etc/profile.d/conda.sh";
  fi
}

function activate_env()
{
  if [[ $ECE_VERBOSE == 0 ]]; then echo ... Activating environment ...; fi
  conda activate ${LOGOS_LIBS_NAME}
}

function install_libraries()
{
  if [[ $ECE_VERBOSE == 0 ]]; then echo Installing libraries ...; fi
  local COMMAND=`echo conda install python numpy pandas scikit-learn`
  echo ... conda command: ${COMMAND}
  ${COMMAND}
  # conda-forge
  if [[ $ECE_VERBOSE == 0 ]]; then echo ... Installing libraries from conda-forge ...; fi
  if [[ "$OSOPTION" = "--windows" ]];
  then
    local COMMAND=`echo conda install -c conda-forge pyomo ipopt glpk pyutilib numpy-financial`
  else
    local COMMAND=`echo conda install -c conda-forge pyomo ipopt coincbc glpk pyutilib numpy-financial`
  fi
  if [[ $ECE_VERBOSE == 0 ]]; then echo ... conda-forge command: ${COMMAND}; fi
  ${COMMAND}
  activate_env
}

function create_libraries()
{
  if [[ $ECE_VERBOSE == 0 ]]; then echo Installing libraries ...; fi
  local COMMAND=`echo conda create -n ${LOGOS_LIBS_NAME} python numpy pandas scikit-learn`
  echo ... conda command: ${COMMAND}
  ${COMMAND}
  # conda-forge
  if [[ $ECE_VERBOSE == 0 ]]; then echo ... Installing libraries from conda-forge ...; fi
  if [[ "$OSOPTION" = "--windows" ]];
  then
    local COMMAND=`echo conda install -n ${LOGOS_LIBS_NAME} -c conda-forge pyomo ipopt glpk pyutilib numpy-financial`
  else
    local COMMAND=`echo conda install -n ${LOGOS_LIBS_NAME} -c conda-forge pyomo ipopt coincbc glpk pyutilib numpy-financial`
  fi
  if [[ $ECE_VERBOSE == 0 ]]; then echo ... conda-forge command: ${COMMAND}; fi
  ${COMMAND}
  activate_env
}

function display_usage()
{
	echo ''
	echo '  ------------------------------------------'
	echo '  Description:'
	echo '      This loads the LOGOS conda environment specified in \$LOGOS_LIBS_NAME \(default LOGOS_libraries\).'
	echo '      This script is also used for installing these libraries\; see options below.'
	echo '  ------------------------------------------'
	echo ''
	echo '  Options:'
	echo '    --conda-defs'
	echo '      Defines location of conda definitions (often miniconda3/etc/profile.d/conda.sh). If not provided, guesses based on OS.'
	echo ''
	echo '    --help'
	echo '      Displays this text and exits'
	echo ''
	echo '    --install'
	echo '      Installs current python library versions for this release of LOGOS using conda'
	echo ''
	echo '    --load'
	echo '      Attempts to activate LOGOS conda environment without installation'
	echo ''
	echo '    --quiet'
	echo '      Runs script with minimal output'
	echo ''
}

# main

# set default operation
ECE_MODE=1 # 1 for loading, 2 for install, 0 for help
INSTALL_OPTIONAL="" # --optional if installing optional, otherwise blank
ECE_VERBOSE=0 # 0 for printing, anything else for no printing
ECE_CLEAN=0 # 0 for yes (remove LOGOS libs env before installing), 1 for don't remove it
LOGOS_LIBS_NAME=LOGOS_libraries
CONDA_DEFS=""

# parse command-line arguments
while test $# -gt 0
do
  case "$1" in
    --help)
      display_usage
      return
      ;;
    --load)
      ECE_MODE=1
      ;;
    --install)
      ECE_MODE=2
      ;;
    --quiet)
      ECE_VERBOSE=1
      ;;
    --conda-defs)
      shift
      CONDA_DEFS=$1
      ;;
  esac
  shift
done

if [[ $ECE_VERBOSE == 0 ]];
then
  echo ... Run Options:
  echo ...    Mode: $ECE_MODE
  echo ...   Verbosity: $ECE_VERBOSE
  echo ...   Clean: $ECE_CLEAN
  echo ...   Conda Defs: $CONDA_DEFS
  if [[ $ECE_MODE == 1 ]];
  then
    echo ... Loading LOGOS libraries ...
  elif [[ $ECE_MODE == 2 ]];
  then
    echo ... Installing LOGOS libraries ...
  fi
fi

# determine operating system
establish_OS
if [[ $ECE_VERBOSE == 0 ]]; then echo ... Detected OS as ${OSOPTION} ...; fi
if [[ $ECE_VERBOSE == 0 ]]; then echo ... \>\> LOGOS conda environment is named \"${LOGOS_LIBS_NAME}\"; fi

if [[ "$CONDA_DEFS" = "" ]]; then find_conda_defs; fi

if test -e ${CONDA_DEFS};
then
	if [[ $ECE_VERBOSE == 0 ]]; then echo ... Found conda definitions at ${CONDA_DEFS}; fi
  source ${CONDA_DEFS}
else
  echo ... Conda definitions not found at \"${CONDA_DEFS}\"!
  echo ... \>\> Specify the location of miniconda3/etc/profile.d/conda.sh through the --conda-defs option.
  return 1
fi

# debug output conda version
if [[ $ECE_VERBOSE == 0 ]]; then echo `conda -V`; fi

# find RAVEN libraries environment
if conda env list | grep ${LOGOS_LIBS_NAME};
then
  if [[ $ECE_VERBOSE == 0 ]]; then echo ... Found library environment ...; fi
  LIBS_EXIST=0
else
  if [[ $ECE_VERBOSE == 0 ]]; then echo ... Did not find library environment ...; fi
  LIBS_EXIST=1
fi

# take action depending on mode
## load only
if [[ $ECE_MODE == 1 ]];
then
  # as long as library env exists, activate it
  if [[ $LIBS_EXIST == 0 ]];
  then
    activate_env
  # if it doesn't exist, make some noise.
  else
    echo conda environment ${LOGOS_LIBS_NAME} not found!
    return 1
  fi
fi

## install mode
if [[ $ECE_MODE == 2 ]];
then
  # if libraries already exist, depends on if in "clean" mode or not
  if [[ $LIBS_EXIST == 0 ]];
  then
    # if libs exist and clean mode, scrub them
    if [[ $ECE_CLEAN == 0 ]];
    then
      if [[ $ECE_VERBOSE == 0 ]]; then echo ... Removing old environment ...; fi
      conda deactivate
      conda remove -n ${LOGOS_LIBS_NAME} --all -y
      create_libraries
    # if libs exist, but not clean mode, install;
    else
      install_libraries
    fi
  # if libraries don't exist, create them
  else
    create_libraries
  fi
fi

if [[ $ECE_VERBOSE == 0 ]]; then echo  ... done!; fi
