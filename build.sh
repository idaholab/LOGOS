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
  # default location of conda definitions, windows is unsurprisingly an exception
  if [[ "$OSOPTION" = "--windows" ]];
  then
    CONDA_DEFS="/c/ProgramData/Miniconda3/etc/profile.d/conda.sh";
  else
    CONDA_DEFS="$HOME/miniconda3/etc/profile.d/conda.sh";
  fi
}

function activate_env()
{
  if [[ $ECE_VERBOSE == 0 ]]; then echo ... Activating environment ...; fi
  COMMAND=`source activate ${RIAM_LIBS_NAME}`
  ${COMMAND}
}

function install_libraries()
{
  if [[ $ECE_VERBOSE == 0 ]]; then echo Installing libraries ...; fi
  local COMMAND=`echo conda install numpy pandas`
  echo ... conda command: ${COMMAND}
  ${COMMAND}
  # conda-forge
  if [[ $ECE_VERBOSE == 0 ]]; then echo ... Installing libraries from conda-forge ...; fi
  local COMMAND=`echo conda install -c conda-forge pyomo ipopt coincbc glpk`
  if [[ $ECE_VERBOSE == 0 ]]; then echo ... conda-forge command: ${COMMAND}; fi
  ${COMMAND}
  activate_env
}

function create_libraries()
{
  if [[ $ECE_VERBOSE == 0 ]]; then echo Installing libraries ...; fi
  local COMMAND=`echo conda create -n ${RIAM_LIBS_NAME} numpy pandas`
  echo ... conda command: ${COMMAND}
  ${COMMAND}
  # conda-forge
  if [[ $ECE_VERBOSE == 0 ]]; then echo ... Installing libraries from conda-forge ...; fi
  local COMMAND=`echo conda install -n ${RIAM_LIBS_NAME} -c conda-forge pyomo ipopt coincbc glpk`
  if [[ $ECE_VERBOSE == 0 ]]; then echo ... conda-forge command: ${COMMAND}; fi
  ${COMMAND}
  activate_env
}

function display_usage()
{
	echo ''
	echo '  ------------------------------------------'
	echo '  Description:'
	echo '      This loads the RIAM conda environment specified in \$RIAM_LIBS_NAME \(default riam_libraries\).'
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
	echo '      Installs current python library versions for this release of RIAM using conda'
	echo ''
	echo '    --load'
	echo '      Attempts to activate RIAM conda environment without installation'
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
ECE_CLEAN=0 # 0 for yes (remove riam libs env before installing), 1 for don't remove it
RIAM_LIBS_NAME=riam_libraries

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
    echo ... Loading RIAM libraries ...
  elif [[ $ECE_MODE == 2 ]];
  then
    echo ... Installing RIAM libraries ...
  fi
fi

# determine operating system
establish_OS
if [[ $ECE_VERBOSE == 0 ]]; then echo ... Detected OS as ${OSOPTION} ...; fi
if [[ $ECE_VERBOSE == 0 ]]; then echo ... \>\> RIAM conda environment is named \"${RIAM_LIBS_NAME}\"; fi

# establish conda function definitions (conda 4.4+ ONLY, 4.3 and before not supported)
find_conda_defs
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
if conda env list | grep ${RIAM_LIBS_NAME};
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
    echo conda environment ${RIAM_LIBS_NAME} not found!
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
      conda remove -n ${RIAM_LIBS_NAME} --all -y
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
