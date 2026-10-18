package main

import (
	"fmt"
	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
	"github.com/hyperledger/fabric-contract-api-go/v2/metadata"
	"github.com/hyperledger/fabric-contract-api-go/v2/serializer"
	"math"
	"reflect"
	"strconv"
)

// The default schema validator can panic on NaN/Inf before a contract method
// runs. Validate floats inside its supported serializer extension point, before
// the schema validator; outer recovery cannot catch its conversion goroutines.
type finiteJSONSerializer struct{ serializer.JSONSerializer }

func (s *finiteJSONSerializer) FromString(param string, fieldType reflect.Type, parameter *metadata.ParameterMetadata, components *metadata.ComponentMetadata) (reflect.Value, error) {
	if fieldType.Kind() == reflect.Float64 || fieldType.Kind() == reflect.Float32 {
		number, err := strconv.ParseFloat(param, fieldType.Bits())
		if err != nil || math.IsNaN(number) || math.IsInf(number, 0) {
			return reflect.Value{}, fmt.Errorf("numeric argument must be finite")
		}
	}
	return s.JSONSerializer.FromString(param, fieldType, parameter, components)
}

func newMisinformationChaincode() (*contractapi.ContractChaincode, error) {
	code, err := contractapi.NewChaincode(&MisinformationContract{})
	if err == nil {
		code.TransactionSerializer = &finiteJSONSerializer{}
	}
	return code, err
}
