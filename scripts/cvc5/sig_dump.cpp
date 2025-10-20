#include <clang/AST/AST.h>
#include <clang/AST/ASTContext.h>
#include <clang/AST/Decl.h>
#include <clang/AST/DeclCXX.h>
#include <clang/AST/Type.h>
#include <clang/ASTMatchers/ASTMatchFinder.h>
#include <clang/ASTMatchers/ASTMatchers.h>
#include <clang/Basic/SourceManager.h>
#include <clang/Frontend/CompilerInstance.h>
#include <clang/Tooling/CommonOptionsParser.h>
#include <clang/Tooling/Tooling.h>
#include <llvm/Support/CommandLine.h>
#include <llvm/Support/Path.h>
#include <iostream>
#include <memory>
#include <set>
#include <string>
#include <vector>

using namespace clang;
using namespace clang::tooling;
using namespace clang::ast_matchers;

namespace {

llvm::cl::OptionCategory SigDumpCategory("sig-dump options");
llvm::cl::list<std::string> FilterFiles(
    "files", llvm::cl::desc("Input source files to scan (space-separated)"),
    llvm::cl::ZeroOrMore, llvm::cl::Positional, llvm::cl::cat(SigDumpCategory));
// Note: Build path option '-p' is provided by CommonOptionsParser; don't re-register it here.

class SigCollector : public MatchFinder::MatchCallback {
 public:
  explicit SigCollector(const std::set<std::string>& filter)
      : filterFiles(filter) {}

  void run(const MatchFinder::MatchResult& Result) override {
    const SourceManager* SM = Result.SourceManager;
    const LangOptions& LO = Result.Context->getLangOpts();

    const FunctionDecl* FD =
        Result.Nodes.getNodeAs<FunctionDecl>("funcDecl");
    if (!FD || !FD->isThisDeclarationADefinition()) return;

    SourceLocation Loc = FD->getLocation();
    if (Loc.isInvalid() || !SM->isInMainFile(Loc)) {
      // We only consider entities in user files. We'll filter by file path below.
    }

    std::string FilePath = SM->getFilename(Loc).str();
    if (!filterFiles.empty() && filterFiles.count(FilePath) == 0) {
      return;
    }

    // Build qualified function name
    std::string qualifiedName = FD->getQualifiedNameAsString();

    // Build parameter list
    PrintingPolicy PP(LO);
    PP.FullyQualifiedName = true;
    PP.SuppressScope = false;
    PP.PrintCanonicalTypes = false;
    PP.SuppressUnwrittenScope = false;
    PP.IncludeNewlines = false;

    std::string params;
    params.push_back('(');
    for (unsigned i = 0, n = FD->getNumParams(); i < n; ++i) {
      const ParmVarDecl* P = FD->getParamDecl(i);
      QualType T = P->getType();
      if (i > 0) params.append(", ");
      params.append(T.getAsString(PP));
    }
    params.push_back(')');

    // Append const for methods
    if (const auto* MD = llvm::dyn_cast<CXXMethodDecl>(FD)) {
      if (MD->isConst()) {
        params.append(" const");
      }
    }

    // Line number
    unsigned line = SM->getSpellingLineNumber(Loc);

    // Print: path:qualified(params):line
    std::cout << FilePath << ":" << qualifiedName << params << ":" << line
              << "\n";
  }

 private:
  std::set<std::string> filterFiles;
};

}  // namespace

int main(int argc, const char** argv) {
  auto ExpectedParser = CommonOptionsParser::create(argc, argv, SigDumpCategory);
  if (!ExpectedParser) {
    llvm::errs() << ExpectedParser.takeError();
    return 1;
  }
  CommonOptionsParser& OptionsParser = ExpectedParser.get();

  ClangTool Tool(OptionsParser.getCompilations(), OptionsParser.getSourcePathList());

  std::set<std::string> filter;
  for (const auto& f : FilterFiles) filter.insert(f);

  SigCollector Collector(filter);
  MatchFinder Finder;
  auto FuncMatcher = functionDecl(isDefinition()).bind("funcDecl");
  Finder.addMatcher(FuncMatcher, &Collector);

  return Tool.run(newFrontendActionFactory(&Finder).get());
}


