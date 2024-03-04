# Copyright 2024 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

DESCRIPTION="Script to automatically deploy binpkgs to github"
HOMEPAGE="https://github.com/gentoo-binhost/gh-binhost"
SRC_URI="https://github.com/gentoo-binhost/gh-binhost/archive/refs/tags/v0.1.tar.gz"

LICENSE=""
SLOT="0"
KEYWORDS="amd64 arm arm64"

DEPEND="
	dev-python/PyGithub
	sys-apps/portage
"
RDEPEND="${DEPEND}"
BDEPEND=""

src_install() {
	dosbin ${S}/src/gh_deploy.py
	insinto /etc/portage
	doins ${S}/src/bashrc
}
