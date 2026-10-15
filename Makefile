.PHONY: build test examples verify
build:
	cargo build --release
test:
	cargo fmt --check
	cargo clippy --all-targets -- -D warnings
	cargo test
	PYTHONPATH=python python3 -m unittest discover -s tests -p 'test_runner.py' -v
	PYTHONPATH=python python3 -m unittest discover -s tests -p 'test_report.py' -v
examples:
	npm ci --prefix examples/stripe-credits
	npm ci --prefix examples/opensaas
	cd examples/opensaas && DATABASE_URL="$$COMMITCUT_DATABASE_URL" npx prisma generate
verify: build
	PYTHONPATH=python python3 scripts/verify-examples.py --upstream --output .runs/verify-$$(date +%s)
