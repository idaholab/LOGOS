LATEX_FLAGS=-interaction=nonstopmode

all: default software_design_description.pdf

software_design_description.pdf : software_design_description.tex software_design_description.bib DesignStakeHoldersAndConcerns.tex Intro.tex ../../version.tex SoftwareDesing.tex ravenDepAndLimitations.tex otherDesignDocs.tex dataInterfaceSystemControl.tex
	pdflatex $(LATEX_FLAGS) software_design_description.tex
	bibtex software_design_description
	pdflatex $(LATEX_FLAGS) software_design_description.tex
	pdflatex $(LATEX_FLAGS) software_design_description.tex

../../version.tex : *.tex
	git log -1 --format="%H %an %aD" .. > ../../version.tex

default: *.tex
	python ../../../../../scripts/library_handler.py manual > dependencies.tex

.PHONY: clean

clean:
	@rm -f *~ *.aux *.bbl *.blg *.log *.out *.toc *.lot *.lof software_design_description.pdf dependencies.tex
