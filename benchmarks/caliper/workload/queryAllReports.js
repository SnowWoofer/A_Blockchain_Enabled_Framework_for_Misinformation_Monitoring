'use strict';

const { WorkloadModuleBase } = require('@hyperledger/caliper-core');

/**
 * Read benchmark: range-scans the full world state through the gateway peer.
 * Exercises the CouchDB rich-query path that powers GET /reports on the API.
 */
class QueryAllReportsWorkload extends WorkloadModuleBase {
    async submitTransaction() {
        this.txIndex++;

        const request = {
            contractId: this.roundArguments.contractId,
            contractFunction: 'QueryAllReports',
            contractArguments: [],
            readOnly: true
        };

        await this.sutAdapter.sendRequests(request);
    }
}

function createWorkloadModule() {
    return new QueryAllReportsWorkload();
}

module.exports.createWorkloadModule = createWorkloadModule;
