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
	install -Dm755 -d $(DATADIR)/classshare/pdf_annotate
	install -Dm644 pdf_annotate/__init__.py $(DATADIR)/classshare/pdf_annotate/__init__.py
	install -Dm644 pdf_annotate/storage.py $(DATADIR)/classshare/pdf_annotate/storage.py
	install -Dm644 pdf_annotate/ws_relay.py $(DATADIR)/classshare/pdf_annotate/ws_relay.py
	install -Dm644 pdf_annotate/routes.py $(DATADIR)/classshare/pdf_annotate/routes.py
	install -Dm644 pdf_annotate/viewer.html $(DATADIR)/classshare/pdf_annotate/viewer.html
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
	python3 tests/test_pdf_annotate_routes.py
	python3 tests/test_file_lists_and_ids.py
	python3 -m pytest tests/test_core.py -v
