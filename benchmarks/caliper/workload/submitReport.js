'use strict';

const crypto = require('crypto');
const { WorkloadModuleBase } = require('@hyperledger/caliper-core');

/**
 * Write benchmark: anchors synthetic misinformation reports on-chain.
 *
 * Argument order must match the committed chaincode contract exactly:
 *   SubmitReport(ctx, report_id, content_hash, confidence, label,
 *                language, model_version, timestamp, off_chain_uri)
 * All fields are validated by misinformation.go, so every generated value
 * below satisfies those checks (64-hex hash, confidence in [0,1], RFC3339 ts).
 */
class SubmitReportWorkload extends WorkloadModuleBase {
    async submitTransaction() {
        this.txIndex++;

        const reportId = `caliper-${this.workerIndex}-${this.txIndex}-${Date.now()}`;
        const contentHash = crypto.createHash('sha256')
            .update(`payload-${reportId}`)
            .digest('hex'); // 64 hex chars — passes chaincode validation
        const confidence = '0.990000';
        const label = '1';
        const language = this.roundArguments.language || 'eng';
        const modelVersion = 'caliper-v1';
        const timestamp = new Date().toISOString();
        const offChainUri = `ipfs://benchmark/${reportId}`;

        const request = {
            contractId: this.roundArguments.contractId,
            contractFunction: 'SubmitReport',
            contractArguments: [
                reportId,
                language,           // 2. language (nso/zul/eng)
                contentHash,        // 3. contentHash (64-char hex digest)
                label,              // 4. label (0/1)
                confidence,         // 5. confidence (float in [0,1])
                modelVersion,       // 6. model_version
                timestamp,          // 7. RFC3339 timestamp
                offChainUri         // 8. off_chain_uri
            ],
            readOnly: false
        };

        await this.sutAdapter.sendRequests(request);
    }
}

function createWorkloadModule() {
    return new SubmitReportWorkload();
}

module.exports.createWorkloadModule = createWorkloadModule;
