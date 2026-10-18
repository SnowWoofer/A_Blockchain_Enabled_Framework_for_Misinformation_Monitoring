'use strict';

// Caliper 0.7.1 floors txNumber / workers and drops the remainder. Use its
// public message setter to allocate that remainder, then delegate all pacing
// to the unmodified built-in fixed-load controller. One request per invocation.
const fixedLoad = require('@hyperledger/caliper-core/lib/worker/rate-control/fixedLoad');

module.exports.createRateController = (testMessage, stats, workerIndex) => {
    const count = testMessage.getWorkloadSpec().arguments.transactionCount;
    const workers = testMessage.getWorkersNumber();
    if (!Number.isSafeInteger(count) || !Number.isSafeInteger(workers) ||
        count < workers || workers < 1 || !Number.isInteger(workerIndex) ||
        workerIndex < 0 || workerIndex >= workers) {
        throw new Error('Invalid transaction count or worker allocation');
    }
    testMessage.setNumberOfTxs(Math.floor(count / workers) + (workerIndex < count % workers ? 1 : 0));
    return fixedLoad.createRateController(testMessage, stats, workerIndex);
};
