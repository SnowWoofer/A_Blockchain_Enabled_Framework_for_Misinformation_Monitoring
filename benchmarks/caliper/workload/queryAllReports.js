'use strict';
const {RecordedWorkload} = require('./common');
// Full world-state range scan on either LevelDB or CouchDB; not a rich query.
class QueryAllReportsWorkload extends RecordedWorkload {
    async submitTransaction() {
        await this.recorded({contractId:'misinformation',contractFunction:'QueryAllReports',contractArguments:[],
            readOnly:true,invokerIdentity:'factchecker1',invokerMspId:'Org1MSP'});
    }
}
module.exports.createWorkloadModule = () => new QueryAllReportsWorkload();
