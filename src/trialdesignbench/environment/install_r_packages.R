# Install the benchmark's R packages from a dated Posit Package Manager
# snapshot and record the installed versions.
#
# Usage: Rscript install_r_packages.R <snapshot> <ubuntu-codename> <pkgs.txt> <out.json>

args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 4)
snapshot <- args[[1]]
codename <- args[[2]]
pkg_file <- args[[3]]
out_json <- args[[4]]

repo <- sprintf(
  "https://packagemanager.posit.co/cran/__linux__/%s/%s", codename, snapshot
)
# PPM serves Linux binaries only when the user agent names the R platform.
options(
  repos = c(CRAN = repo),
  HTTPUserAgent = sprintf(
    "R/%s R (%s)",
    getRversion(),
    paste(getRversion(), R.version["platform"], R.version["arch"], R.version["os"])
  )
)

pkgs <- trimws(readLines(pkg_file))
pkgs <- pkgs[nzchar(pkgs)]
install.packages(pkgs, Ncpus = max(1L, parallel::detectCores()))

missing <- pkgs[!vapply(pkgs, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing) > 0) {
  stop("failed to install: ", paste(missing, collapse = ", "))
}

# Record the snapshot for anyone inspecting the image. Installs fail offline.
cat(
  sprintf('options(repos = c(CRAN = "%s"))\n', repo),
  file = file.path(R.home("etc"), "Rprofile.site"),
  append = TRUE
)

versions <- vapply(pkgs, function(p) as.character(packageVersion(p)), character(1))
jsonlite::write_json(
  list(
    r_version = as.character(getRversion()),
    cran_snapshot = snapshot,
    repo = repo,
    packages = as.list(versions)
  ),
  out_json,
  auto_unbox = TRUE,
  pretty = TRUE
)
print(versions)
