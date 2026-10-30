CFLAGS ?= -O2 -g -Wall -Wextra -Wno-unused-parameter
# without libpam0g-dev installed: make PAM_INCLUDE=<dir with security/*.h>
ifdef PAM_INCLUDE
CFLAGS += -I$(PAM_INCLUDE)
PAM_LIB = /usr/lib/$(shell gcc -print-multiarch)/libpam.so.0
else
PAM_LIB = -lpam
endif
all: pam_face.so pam_face_test

pam_face.so: pam_face.c
	$(CC) $(CFLAGS) -fPIC -shared -o $@ $< $(PAM_LIB)

pam_face_test: pam_face.c
	$(CC) $(CFLAGS) -DFACELOCK_TEST -o $@ $<

clean:
	rm -f pam_face.so pam_face_test
