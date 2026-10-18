'use strict';
const crypto = require('node:crypto');
const {RecordedWorkload} = require('./common');
class SubmitReportWorkload extends RecordedWorkload {
    async submitTransaction() {
        const id = `${this.roundArguments.runId}-${this.roundArguments.label}-${this.workerIndex}-${++this.txIndex}`;
        const digest = crypto.createHash('sha256').update(id).digest('hex');
        await this.recorded({contractId:'misinformation', contractFunction:'Submit',
            contractArguments:[id,digest,'1',this.roundArguments.rejectWrites ? '2' : '.99','caliper-v3',new Date().toISOString()],
            readOnly:false, invokerIdentity:'official1', invokerMspId:'Org1MSP'});
    }
}
module.exports.createWorkloadModule = () => new SubmitReportWorkload();
