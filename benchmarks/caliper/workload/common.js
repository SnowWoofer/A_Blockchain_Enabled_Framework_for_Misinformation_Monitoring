'use strict';
const fs = require('node:fs');
const {performance} = require('node:perf_hooks');
const {WorkloadModuleBase} = require('@hyperledger/caliper-core');
class RecordedWorkload extends WorkloadModuleBase {
    async initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext) {
        await super.initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext);
        this.txIndex = 0;
        this.file = `/caliper/workspace/transactions-${workerIndex}.jsonl`;
    }
    async recorded(request) {
        const start = Date.now(), clock = performance.now();
        let success = false, error = '', txId = '';
        try {
            const returned = await this.sutAdapter.sendRequests(request);
            const statuses = Array.isArray(returned) ? returned : [returned];
            success = statuses.length > 0 && statuses.every(s => s && s.GetStatus() === 'success');
            txId = statuses[0]?.GetID() || '';
            if (!success) error = 'Caliper reported failed/unverified transaction';
        } catch (e) { error = String(e.message).slice(0, 300); }
        const end = Date.now(), latency = performance.now() - clock;
        fs.appendFileSync(this.file, JSON.stringify({round:this.roundArguments.label, worker:this.workerIndex,
            success, error, tx_id:txId, started_ms:start, finished_ms:end, latency_ms:latency})+'\n');
    }
}
module.exports = {RecordedWorkload};
