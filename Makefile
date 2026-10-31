PY ?= python

.PHONY: setup test lint demo hindcast simulate cost live asl validate-infra bundle deploy data app

setup:            ## virtualenv with dev dependencies
	$(PY) -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

test:             ## 57 tests, including every Lambda handler against mocked AWS
	$(PY) -m pytest -q

lint:
	ruff check .

demo:             ## a group on the Annapurna Circuit in October 2014, replayed through the engine
	$(PY) -m bato demo

hindcast:         ## the storm-hold rule scored on 10 years of real forecasts (charts in docs/img)
	$(PY) -m bato hindcast

simulate:         ## a 1,500-group season under six alert policies (~6 minutes)
	$(PY) -m bato simulate

cost:             ## quiet and peak month cost model
	$(PY) -m bato cost

live:             ## today's real GEFS forecast at the six passes (needs eccodes and internet)
	$(PY) -m bato live

asl:              ## regenerate the Step Functions definition from code
	$(PY) -m bato asl > infra/statemachine/escalation.asl.json

validate-infra:
	cfn-lint infra/template.yaml

data:             ## re-pull the weather extracts from the public S3 buckets (~16 GB streamed, ~40 min)
	$(PY) scripts/fetch_gefs_reforecast.py --years 2010-2019 --months 3,4,5,10,11
	$(PY) scripts/fetch_gefs_reforecast.py --dates 2014-10-08:2014-10-15 --members c00,p01,p02,p03,p04 \
	    --out data/processed/gefs_hudhud_2014.csv
	$(PY) scripts/fetch_era5_snowfall.py --years 2010-2019 --months 3,4,5,10,11

bundle:           ## Lambda zip: the package and the trail data, nothing else (boto3 is in the runtime)
	rm -rf build/lambda && mkdir -p build/lambda/data
	cp -r bato build/lambda/ && find build/lambda -name __pycache__ -exec rm -rf {} +
	rm build/lambda/bato/simulate.py build/lambda/bato/report.py build/lambda/bato/hindcast.py \
	   build/lambda/bato/cost.py build/lambda/bato/__main__.py
	cp data/trails.json data/passes.json build/lambda/data/
	touch build/lambda/requirements.txt

deploy: bundle    ## see infra/README.md first
	cd infra && sam build && sam deploy --guided --region ap-south-1 --stack-name bato

app:              ## the Streamlit demo on http://localhost:8501 (pip install -r requirements-app.txt first)
	$(PY) -m streamlit run app.py
