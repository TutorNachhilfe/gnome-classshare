Name:           gnome-classshare
Version:        1.0.0
Release:        1%{?dist}
Summary:        Dateien teilen und einsammeln im Schulnetz

License:        GPL-3.0
URL:            https://github.com/TutorNachhilfe/gnome-classshare
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  make

Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       python3-pillow

%description
GNOME-App für Tutoren zum Teilen und Einsammeln von Dateien
per QR-Code im lokalen Netzwerk.

%prep
%autosetup

%build
# nichts zu kompilieren

%install
%make_install PREFIX=/usr

%files
%license LICENSE
%doc README.md CHANGELOG.md
/usr/share/classshare/
/usr/share/icons/hicolor/scalable/apps/gnome-classshare.svg
/usr/share/applications/gnome-classshare.desktop
/usr/bin/classshare

%changelog
* Thu May 14 2026 TutorNachhilfe <konto@tutor.schule> - 1.0.0-1
- Erste Paketversion
