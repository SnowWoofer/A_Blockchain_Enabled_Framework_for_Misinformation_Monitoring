'use strict';

// Integration with the installed, unmodified Caliper scheduler and controller.
// Run inside the Caliper image with NODE_PATH=/caliper/node_modules.
const assert = require('node:assert/strict');
const path = require('node:path');
const CaliperWorker = require('@hyperledger/caliper-core/lib/worker/caliper-worker');
const RateControl = require('@hyperledger/caliper-core/lib/worker/rate-control/rateControl');
const TestMessage = require('@hyperledger/caliper-core/lib/common/messages/testMessage');

async function verify(count, workers) {
    const totals = await Promise.all(Array.from({length:workers}, async (_, worker) => {
        let submitted = 0, finished = 0;
        const stats = {
            getTotalSubmittedTx: () => submitted,
            getTotalFinishedTx: () => finished,
            getTotalSuccessfulTx: () => finished,
            getTotalFailedTx: () => 0,
            getTotalLatencyForSuccessful: () => finished,
            getTotalLatencyForFailed: () => 0
        };
        const message = new TestMessage('test', [], {
            numb:Math.floor(count/workers), totalWorkers:workers, testRound:0,
            rateControl:{type:path.resolve(__dirname, '../caliper/workload/count-fixed-load.js'),
                opts:{transactionLoad:100,startTps:25}},
            workload:{arguments:{transactionCount:count}}
        });
        const controller = new RateControl(message, stats, worker);
        const workload = {submitTransaction:async () => { submitted++; finished++; }};
        const runner = {
            internalTxObserver:{getCurrentStatistics:() => stats},
            setImmediatePromise:fn => new Promise(resolve => setImmediate(() => { fn(); resolve(); }))
        };
        await CaliperWorker.prototype._runFixedNumber.call(runner, workload, message.getNumberOfTxs(), controller);
        await controller.end();
        assert.equal(submitted, Math.floor(count/workers) + (worker<count%workers ? 1 : 0));
        return submitted;
    }));
    assert.equal(totals.reduce((a,b) => a+b,0), count);
    console.log(JSON.stringify({count,workers,attempts_by_worker:totals,result:'passed'}));
}

(async () => {
    for (const [count,workers] of [[1,1],[5,2],[7,3],[1000,2],[1001,3]]) await verify(count,workers);
})().catch(error => { console.error(error); process.exitCode=1; });
