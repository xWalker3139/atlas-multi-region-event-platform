.PHONY: test demo package format validate postgres-test
test:
	python -m unittest discover -s tests -v
demo:
	python tools/simulate.py --events 20000 --output results/local-simulation.json
package:
	bash tools/package_lambda.sh
format:
	terraform fmt -recursive infra
validate:
	terraform -chdir=infra init -backend=false
	terraform -chdir=infra validate
postgres-test:
	python -m unittest tests.test_postgres -v
