.PHONY: assets check compile serve

assets:
	python3 scripts/build_release_assets.py

compile:
	python3 -m compileall -q pipelines scripts

check: assets compile
	python3 scripts/validate_release.py

serve:
	python3 -m http.server 8000 --directory docs
