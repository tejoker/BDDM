#!/bin/bash
cd ~/DESol
export PATH=$HOME/.elan/bin:$PATH
lake update && lake build 2>&1
