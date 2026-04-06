@echo off
REM Backup private job search data to GitHub
REM Tracks: data/, output/, coaching/, memory/, inbox/
REM Repo: https://github.com/nickmagnuson2-jpg/nick-job-search-data

set GIT=git --git-dir="C:\Users\chris\.nick-private-git" --work-tree="C:\Users\chris\Documents\Projects\Nick-claude-resume-coach"

echo Staging changes...
%GIT% add --force data/ output/ coaching/ memory/ inbox/

echo Committing...
for /f "tokens=1-3 delims=/ " %%a in ("%date%") do set TODAY=%%c-%%a-%%b
%GIT% commit -m "Backup %TODAY%" --allow-empty

echo Pushing to GitHub...
%GIT% push origin master

echo Done. Data backed up to https://github.com/nickmagnuson2-jpg/nick-job-search-data
