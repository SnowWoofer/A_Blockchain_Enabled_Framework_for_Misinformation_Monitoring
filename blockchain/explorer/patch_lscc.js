#!/usr/bin/env node
const fs = require('fs');
const path = '/opt/explorer/app/platform/fabric/gateway/FabricGateway.js';
const content = fs.readFileSync(path, 'utf8');

const oldFunc = `    queryInstantiatedChaincodes(channelName) {
        return __awaiter(this, void 0, void 0, function* () {
            logger.info('queryInstantiatedChaincodes', channelName);
            const network = yield this.gateway.getNetwork(channelName);
            let contract = network.getContract('lscc');
            let result = yield contract.evaluateTransaction('GetChaincodes');
            let resultJson = fabprotos.protos.ChaincodeQueryResponse.decode(result);
            if (resultJson.chaincodes.length <= 0) {
                resultJson = { chaincodes: [], toJSON: null };
                contract = network.getContract('_lifecycle');
                result = yield contract.evaluateTransaction('QueryChaincodeDefinitions', '');
                const decodedReult = fabprotos.lifecycle.QueryChaincodeDefinitionsResult.decode(result);
                for (const cc of decodedReult.chaincode_definitions) {
                    resultJson.chaincodes = (0, concat_1.default)(resultJson.chaincodes, {
                        name: cc.name,
                        version: cc.version
                    });
                }
            }
            logger.debug('queryInstantiatedChaincodes', resultJson);
            return resultJson;
        });
    }`;

const newFunc = `    queryInstantiatedChaincodes(channelName) {
        return __awaiter(this, void 0, void 0, function* () {
            logger.info('queryInstantiatedChaincodes', channelName);
            const network = yield this.gateway.getNetwork(channelName);
            let resultJson = { chaincodes: [], toJSON: null };
            try {
                let contract = network.getContract('lscc');
                let result = yield contract.evaluateTransaction('GetChaincodes');
                resultJson = fabprotos.protos.ChaincodeQueryResponse.decode(result);
            } catch (e) {
                logger.info('lscc not available, falling back to _lifecycle');
            }
            if (resultJson.chaincodes.length <= 0) {
                try {
                    let contract = network.getContract('_lifecycle');
                    let result = yield contract.evaluateTransaction('QueryChaincodeDefinitions', '');
                    const decodedResult = fabprotos.lifecycle.QueryChaincodeDefinitionsResult.decode(result);
                    for (const cc of decodedResult.chaincode_definitions) {
                        resultJson.chaincodes.push({ name: cc.name, version: cc.version });
                    }
                } catch (e2) {
                    logger.error('_lifecycle query also failed', e2);
                }
            }
            logger.debug('queryInstantiatedChaincodes', resultJson);
            return resultJson;
        });
    }`;

if (content.includes(oldFunc)) {
    fs.writeFileSync(path, content.replace(oldFunc, newFunc));
    console.log('Patch applied successfully');
} else {
    console.error('ERROR: Could not find function to patch');
    process.exit(1);
}
