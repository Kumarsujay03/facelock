CFLAGS ?= -O2 -g -Wall -Wextra -Wno-unused-parameter
# without the -dev packages installed: make EXTRA_INCLUDE=<dir with security/ and systemd/ headers>
ifdef EXTRA_INCLUDE
CFLAGS += -I$(EXTRA_INCLUDE)
LIBS = /usr/lib/$(shell gcc -print-multiarch)/libpam.so.0 /usr/lib/$(shell gcc -print-multiarch)/libsystemd.so.0
else
LIBS = -lpam -lsystemd
endif

pam_facelock_keyring.so: pam_facelock_keyring.c
	$(CC) $(CFLAGS) -fPIC -shared -o $@ $< $(LIBS)

clean:
	rm -f pam_facelock_keyring.so
