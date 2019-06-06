LATEX_FLAGS=-interaction=nonstopmode

all: software_requirements_specifications.pdf

software_requirements_specifications.pdf : software_requirements_specifications.tex software_requirements_specifications.bib intro.tex ../../version.tex ../sdd/ravenDepAndLimitations.tex
	python ../../../../../scripts/TestHarness/testers/RavenUtils.py --manual-list > dependencies.tex
	python ../../../../../developer_tools/readRequirementsAndCreateLatex.py -i requirements_list.xml -o requirements.tex 
	pdflatex $(LATEX_FLAGS) software_requirements_specifications.tex
	pdflatex $(LATEX_FLAGS) software_requirements_specifications.tex
	pdflatex $(LATEX_FLAGS) software_requirements_specifications.tex

../../version.tex : *.tex
	git log -1 --format="%H %an %aD" .. > ../../version.tex

default: *.tex
	python ../../../scripts/TestHarness/testers/RavenUtils.py --manual-list > dependencies.tex

.PHONY: clean

clean:
	@rm -f *~ *.aux *.bbl *.blg *.log *.out *.toc *.lot *.lof software_requirements_specifications.pdf dependencies.tex requirements.tex