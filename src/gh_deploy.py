#!/bin/env python3
import os
import socket
import re
import xml.etree.ElementTree as xml
from pathlib import Path
from github import Github, GithubException, UnknownObjectException, InputGitAuthor
import portage

class Block:
    def __init__(self, lines):
        self.lines = lines

    def get(self, key):
        entries = [x for x in self.lines if x.startswith(key + ":")]
        if len(entries) == 0:
            return None
        return entries[0].split(":", 2)[1].strip()

    def set(self, key, value):
        line = key + ": " + value
        entries = [i for i,x in enumerate(self.lines) if x.startswith(key + ":")]
        if len(entries) == 0:
            self.lines.append(line)
        else:
            for i in entries:
                self.lines[i] = line



class Manifest:
    def __init__(self, contents):
        lines = contents.split("\n")

        splits = [i for i,x in enumerate(lines) if x.strip() == ""]

        ci = 0
        self.blocks = []

        for i in splits:
            if i > ci:
                self.blocks.append(Block(lines[ci:i]))
            ci = i + 1

        if ci < len(lines):
            self.blocks.append(Block(lines[ci:len(lines)]))

    def update(self, manifest, package):
        self.blocks[0] = manifest.blocks[0]

        for block in manifest.blocks[1:]:
            if block.get("PATH") == package:
                for i, b in enumerate(self.blocks):
                    if b.get("PATH") == package:
                        self.blocks[i] = block
                        return
                self.blocks.append(block)
                return

    def build(self):
        self.blocks[0].set("PACKAGES", str(len(self.blocks) - 1))

        result = ""
        for block in self.blocks:
            if len(result) > 0:
                result = result + "\n"

            for line in block.lines:
                result = result + line + "\n"

        return result


class PkgConfig:
    def __init__(self):
        self.full_name = os.environ['PF']
        self.name = os.environ['PN']
        self.version = os.environ['PV']
        self.category = os.environ['CATEGORY']
        self.features = os.environ['PORTAGE_FEATURES']
        self.multi_instance = self.features.__contains__('binpkg-multi-instance')
        self.ebuild = os.environ['EBUILD']

        self.dbapi = portage.db[portage.root]["porttree"].dbapi

        pkgdir = os.environ['PKGDIR']

        self.manifest = 'Packages'
        self.manifest_path = pkgdir + '/' + self.manifest

        if self.multi_instance:
            self.file_ext = 'xpak'
            xpack_dir = self.category + '/' + self.name
            
            instances = [name for name in os.listdir(pkgdir + "/" + xpack_dir) if os.path.isfile(os.path.join(pkgdir + "/" + xpack_dir, name)) and name.startswith(self.full_name + '-')]
            build_ids = [int(name[len(self.full_name) + 1:len(name) - len(self.file_ext) - 1]) for name in instances]
            build_id = max(build_ids)
            self.file_name = self.full_name + '-' + str(build_id) + '.' + self.file_ext
            self.pkg_path = xpack_dir + '/' + self.file_name
        else:
            self.file_ext = 'tbz2'
            self.file_name = self.full_name + '.' + self.file_ext
            self.pkg_path = self.category + '/' + self.file_name

        self.file_path = pkgdir + '/' + self.pkg_path
    
    def category_description(self):
        category_metadata_path = Path(self.ebuild).parents[1] / 'metadata.xml'
        if not os.path.isfile(category_metadata_path):
            return 'custom category'
        
        root = xml.parse(category_metadata_path)
        long_description = root.findall('./longdescription[@lang="en"]')

        if len(long_description) > 0:
            long_description = long_description[0].text.strip()
            long_description = re.sub('^\\s*', '', long_description, flags=re.M)
            long_description = re.sub('\n', ' ', long_description, flags=re.M)
        return long_description


    def package_description(self):
        return self.dbapi.aux_get(self.category + "/" + self.full_name, ["DESCRIPTION"])[0]

    

class GitHubConfig:
    def __init__(self, cfg):
        self.repo_name = os.environ['GITHUB_BH_REPO']
        self.token = os.environ['GITHUB_TOKEN']
        self.branch_prefix = 'binhost-'
        self.branch_name = self.branch_prefix + cfg.chost

        self.header_uri = "https://github.com/{}/release/download/{}".format(self.repo_name, self.branch_name)

        self.client = Github(self.token, timeout=280)
        self.repo = self.client.get_repo(self.repo_name)

        self.author = InputGitAuthor("binhost", "binhost" + '@' + socket.getfqdn())

    def ensure_barnch(self):
        try:
            try:
                return self.repo.get_branch(self.branch_name)
            except Exception:
                master_branch = self.repo.get_branch("master")
                return self.repo.create_git_ref(ref='refs/heads/' + self.branch_name, sha=master_branch.commit.sha)
        except Exception:
            print("Unable to ensure '%s' branch!" % gh_branch)
            exit(1)

    def ensure_release(self, pkg, branch):
        release_name = self.branch_name + "/" + pkg.category
        if pkg.multi_instance:
            release_name = release_name + "/" + pkg.name

        try:
            return self.repo.get_release(release_name)
        except Exception:
            description = pkg.package_description() if pkg.multi_instance else pkg.category_description()
            return self.repo.create_git_release(release_name, release_name, description, target_commitish=branch.commit.sha)

    def publish(self, pkg):
        branch = self.ensure_barnch()
        release = self.ensure_release(pkg, branch)

        updated = False

        for asset in release.get_assets():
            if asset.name == pkg.file_name:
                if pkg.multi_instance:
                    print("Package already published")
                    return
                updated = True
                asset.delete_asset()

        release.upload_asset(path=pkg.file_path, content_type='application/x-tar', name=pkg.file_name)
        print('Uploaded ' + pkg.file_name)

        try:
            commitMsg = pkg.category + "-" + pkg.version + (" updated" if updated else " added")
            manifest = ""
            with open(pkg.manifest_path, 'r') as file:
                manifest = file.read()

            def insert_uri(match):
                return match.group(1) + "URI: {}\n".format(self.header_uri) + match.group(2)

            manifest = re.sub(r'(PROFILE:.*\n)(TIMESTAMP:.*\n)', insert_uri, manifest)

            # receive git file/blob reference via git tree
            ref = self.repo.get_git_ref(f'heads/{self.branch_name}')
            tree = self.repo.get_git_tree(ref.object.sha).tree
            sha = [x.sha for x in tree if x.path == pkg.manifest]  # get file sha

            if not sha:
                self.repo.create_file(pkg.manifest, commitMsg, manifest, branch=self.branch_name, committer=self.author)
            else:
                old_manifest = Manifest(self.repo.get_contents(pkg.manifest, ref=self.branch_name).decoded_content.decode())
                new_manifest = Manifest(manifest)

                old_manifest.update(new_manifest, pkg.pkg_path)

                self.repo.update_file(pkg.manifest, commitMsg, old_manifest.build(), sha[0], branch=self.branch_name, committer=self.author)
        except Exception as e:
            #print('error handling Manifest under: ' + pkg.manifest_path + ' Error: ' + str(e))
            raise e
            #exit(1)
        print('Package index updated')

class Config:
    def __init__(self):
        self.chost = os.environ['CHOST']
        self.github = GitHubConfig(self)


if "GITHUB_TOKEN" in os.environ and "GITHUB_BH_REPO" in os.environ:
    cfg = Config()
    pkg = PkgConfig()

    cfg.github.publish(pkg)
else:
    print("Skip binpkg deploy because of missong GITHUB_TOKEN or GITHUB_BH_REPO")

