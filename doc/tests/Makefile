SRCFILE = regression_tests_documentation
TEST_FILES = regression_tests_documentation.tex
LATEX_FLAGS=-interaction=nonstopmode
all: tidy

tidy: $(TESTS_FILES)
	python createRegressionTestDocumentation.py
	pdflatex $(LATEX_FLAGS) ${SRCFILE}.tex
	pdflatex $(LATEX_FLAGS) ${SRCFILE}.tex
	pdflatex $(LATEX_FLAGS) ${SRCFILE}.tex
	
.PHONY: clean

clean:
	@rm -f *~ *.aux *.bbl *.blg *.log *.out *.toc *.lot *.lof $(SRCFILE).pdf

version.tex : $(MANUAL_FILES)
	git log -1 --format="%H %an %aD" .. > version.tex
