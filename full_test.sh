#!/bin/bash
# Enable extended globbing
shopt -s extglob

SUITE_PASS=true
LOGNAME=$(mktemp results_full_test.XXXX)

echo "Running Full Test. Results available in $LOGNAME"

# We pre-build to avoid potential timeouts on a fresh clone
echo "Pre-building base workloads" | tee -a $LOGNAME
./marshal build test/br-base.json
./marshal build test/fedora-base.json

echo "Running launch timeout test (should timeout):" | tee -a $LOGNAME
echo "This test will reset your terminal"
./marshal test test/timeout-run.json | grep "timeout while running"
# Note: This records the 
res=${PIPESTATUS[1]}
reset
echo "Ran launch timeout test (screen was reset)"
if [ $res != 0 ]; then
  echo "Failure" | tee -a $LOGNAME
  SUITE_PASS=false
else
  echo "Success" | tee -a $LOGNAME
fi

echo "Running build timeout test (should timeout):" | tee -a $LOGNAME
./marshal test test/timeout-build.json | grep "timeout while building"
if [ ${PIPESTATUS[1]} != 0 ]; then
  echo "Failure" | tee -a $LOGNAME
  SUITE_PASS=false
else
  echo "Success" | tee -a $LOGNAME
fi

# Run the bulk tests (all work with the 'test' command)
# Note the funny extended globbing, these are just lists of tests that
# shouldn't be tested (e.g. we exclude the base configs and some specialized
# tests)
echo "Running regular tests" | tee -a $LOGNAME
BULK_EXCLUDE="(br-base|fedora-base|incremental|clean|timeout-build|timeout-run|bare|dummy-bare|spike-jobs|spike)"
./marshal clean test/!$BULK_EXCLUDE.json | tee -a $LOGNAME
./marshal test test/!$BULK_EXCLUDE.json | tee -a $LOGNAME
if [ ${PIPESTATUS[0]} != 0 ]; then
  echo "Failure" | tee -a $LOGNAME
  SUITE_PASS=false
else
  echo "Success" | tee -a $LOGNAME
fi

# These tests need to run on spike, but not with the initramfs option
echo "Running bare-metal tests" | tee -a $LOGNAME
IS_INCLUDE="@(bare|dummy-bare|spike|spike-jobs)"
./marshal clean test/$IS_INCLUDE.json | tee -a $LOGNAME
# This is a temporary workaround for bug #38
./marshal build test/spike.json
./marshal test -s test/$IS_INCLUDE.json | tee -a $LOGNAME
if [ ${PIPESTATUS[0]} != 0 ]; then
  echo "Failure" | tee -a $LOGNAME
  SUITE_PASS=false
else
  echo "Success" | tee -a $LOGNAME
fi

# Run the initramfs versions on spike, initramfs runs have many restrictions,
# we only run a few tests here to test basic capabilities
echo "Running initramfs capable tests on spike" | tee -a $LOGNAME
IS_INCLUDE="@(command|flist|host-init|jobs|linux-src|overlay|post-run-hook|run|smoke0)"
./marshal -i clean test/$IS_INCLUDE.json | tee -a $LOGNAME
./marshal -i test -s test/$IS_INCLUDE.json | tee -a $LOGNAME
if [ ${PIPESTATUS[0]} != 0 ]; then
  echo "Failure" | tee -a $LOGNAME
  SUITE_PASS=false
else
  echo "Success" | tee -a $LOGNAME
fi

# Run the specialized tests (tests that are too complicated for ./marshal
# test)
echo "Running clean test" | tee -a $LOGNAME
./test/clean/test.py >> $LOGNAME 
if [ ${PIPESTATUS[0]} != 0 ]; then
  echo "Failure" | tee -a $LOGNAME
  SUITE_PASS=false
fi

echo "Running incremental test" | tee -a $LOGNAME
./test/incremental/test.py >> $LOGNAME
if [ ${PIPESTATUS[0]} != 0 ]; then
  echo "Failure" | tee -a $LOGNAME
  SUITE_PASS=false
  exit 1
fi

echo -e "\n\nMarshal full test complete. Log at: $LOGNAME"
if [ $SUITE_PASS = false ]; then
  echo "FAILURE: Some tests failed" | tee -a $LOGNAME
  exit 1
else
  echo "SUCCESS: Full test success" | tee -a $LOGNAME
  exit 0
fi
