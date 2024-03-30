#!/bin/bash
# A simple wrapper script for loading environment variables from a file, and running dumbhfdl that way.
# intended to be run from the dumbhfdl directory.

# copy the starter environment file (eg. "airspyhf.env"  to ".env", the run "dumbhfdl.sh")

set -o allexport; 
source "${1-.env}"
set +o allexport

if ! [[ -r $(which dumphfdl) ]] ; then
    export PATH="/usr/local/bin:$PATH"
    echo added local bin path
fi

./dumbhfdl.py
