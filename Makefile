PREFIX ?= /usr
DESTDIR ?=
DATADIR = $(DESTDIR)$(PREFIX)/share
BINDIR  = $(DESTDIR)$(PREFIX)/bin

APPFILES = app.py window.py handler.py state.py utils.py constants.py \
           desktop_integration.py qr_utils.py \
           student.html shortcuts.ui

.PHONY: install uninstall test


install:
	install -Dm755 -d $(DATADIR)/classshare
	for f in $(APPFILES); do \
		test -f $$f && install -Dm644 $$f $(DATADIR)/classshare/$$f || true; \
	done
	install -Dm644 icons/classshare.svg \
		$(DATADIR)/icons/hicolor/scalable/apps/gnome-classshare.svg
	install -Dm644 data/gnome-classshare.desktop \
		$(DATADIR)/applications/gnome-classshare.desktop
	install -Dm755 data/classshare.sh $(BINDIR)/classshare
	gtk-update-icon-cache -f -t $(DATADIR)/icons/hicolor || true
	update-desktop-database $(DATADIR)/applications || true

uninstall:
	rm -rf $(DATADIR)/classshare
	rm -f $(DATADIR)/icons/hicolor/scalable/apps/gnome-classshare.svg
	rm -f $(DATADIR)/applications/gnome-classshare.desktop
	rm -f $(BINDIR)/classshare
	gtk-update-icon-cache -f -t $(DATADIR)/icons/hicolor || true
	update-desktop-database $(DATADIR)/applications || true

test:
	python3 tests/test_utils.py
