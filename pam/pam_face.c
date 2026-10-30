/*
 * pam_face: face unlock for the GDM login and lock screens.
 *
 * Asks facelockd (over its root-only socket) to scan for the user's face,
 * passes its progress messages on as PAM info messages (the lock screen
 * extension reads the "facelock:*" ones), and returns its answer.
 *
 * Only works in PAM services whose name starts with "gdm-face", so it
 * can't end up guarding sudo or anything else by mistake.
 *
 * Options: socket=<path>  timeout=<seconds, default 30>
 */
#define _GNU_SOURCE
#define PAM_SM_AUTH
#include <security/pam_modules.h>
#include <security/pam_ext.h>

#include <ctype.h>
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <syslog.h>
#include <time.h>
#include <unistd.h>

#define DEFAULT_SOCKET "/run/facelock/socket"
#define LINE_MAX_LEN 512

typedef void (*message_fn) (void *data, const char *text);

static int
valid_user (const char *user)
{
  if (!user || !*user || strlen (user) > 64 || user[0] == '-' || user[0] == '.')
    return 0;
  for (const char *c = user; *c; c++)
    if (!isalnum ((unsigned char) *c) && *c != '_' && *c != '-' && *c != '.')
      return 0;
  return 1;
}

static double
now (void)
{
  struct timespec ts;
  clock_gettime (CLOCK_MONOTONIC, &ts);
  return ts.tv_sec + ts.tv_nsec / 1e9;
}

/* Returns PAM_SUCCESS, PAM_AUTH_ERR or PAM_AUTHINFO_UNAVAIL. */
static int
facelock_auth (const char *socket_path, const char *user, int timeout,
               message_fn on_message, void *data, char *why, size_t why_size)
{
  struct sockaddr_un addr = { .sun_family = AF_UNIX };
  char line[LINE_MAX_LEN], req[128];
  size_t used = 0;
  double deadline = now () + timeout;
  int fd, result = PAM_AUTHINFO_UNAVAIL, finished = 0;

  snprintf (why, why_size, "no answer from facelockd");
  if (strlen (socket_path) >= sizeof addr.sun_path)
    return PAM_AUTHINFO_UNAVAIL;
  strcpy (addr.sun_path, socket_path);

  fd = socket (AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (fd < 0)
    return PAM_AUTHINFO_UNAVAIL;
  if (connect (fd, (struct sockaddr *) &addr, sizeof addr) < 0)
    {
      snprintf (why, why_size, "facelockd is not running (%s)", strerror (errno));
      close (fd);
      return PAM_AUTHINFO_UNAVAIL;
    }
  snprintf (req, sizeof req, "AUTH %s\n", user);
  if (send (fd, req, strlen (req), MSG_NOSIGNAL) != (ssize_t) strlen (req))
    {
      close (fd);
      return PAM_AUTHINFO_UNAVAIL;
    }

  for (;;)
    {
      struct pollfd pfd = { .fd = fd, .events = POLLIN };
      double left = deadline - now ();
      ssize_t n;
      char *nl;

      if (left <= 0)
        {
          snprintf (why, why_size, "facelockd timed out");
          break;
        }
      n = poll (&pfd, 1, (int) (left * 1000) + 1);
      if (n < 0 && errno == EINTR)
        continue;
      if (n <= 0)
        continue;
      n = recv (fd, line + used, sizeof line - 1 - used, 0);
      if (n < 0 && errno == EINTR)
        continue;
      if (n <= 0)
        break;
      used += n;
      line[used] = '\0';
      while ((nl = strchr (line, '\n')))
        {
          *nl = '\0';
          if (strncmp (line, "MSG ", 4) == 0)
            on_message (data, line + 4);
          else if (strcmp (line, "OK") == 0)
            result = PAM_SUCCESS, finished = 1;
          else if (strncmp (line, "FAIL", 4) == 0)
            {
              result = PAM_AUTH_ERR, finished = 1;
              snprintf (why, why_size, "%.200s", line[4] ? line + 5 : "no match");
            }
          else if (strncmp (line, "UNAVAIL", 7) == 0)
            {
              result = PAM_AUTHINFO_UNAVAIL, finished = 1;
              snprintf (why, why_size, "%.200s", line[7] ? line + 8 : "unavailable");
            }
          used -= nl + 1 - line;
          memmove (line, nl + 1, used + 1);
          if (finished)
            goto done;
        }
      if (used == sizeof line - 1)
        used = 0; /* overlong line: drop it */
    }
done:
  close (fd);
  return result;
}

#ifndef FACELOCK_TEST
static void
pam_message (void *data, const char *text)
{
  pam_info ((pam_handle_t *) data, "%s", text);
}

PAM_EXTERN int
pam_sm_authenticate (pam_handle_t *pamh, int flags, int argc, const char **argv)
{
  const char *user = NULL, *service = NULL, *socket_path = DEFAULT_SOCKET;
  char why[256];
  int timeout = 30, ret;

  for (int i = 0; i < argc; i++)
    {
      if (strncmp (argv[i], "socket=", 7) == 0)
        socket_path = argv[i] + 7;
      else if (strncmp (argv[i], "timeout=", 8) == 0)
        timeout = atoi (argv[i] + 8) > 0 ? atoi (argv[i] + 8) : timeout;
    }

  if (pam_get_item (pamh, PAM_SERVICE, (const void **) &service) != PAM_SUCCESS || !service ||
      strncmp (service, "gdm-face", 8) != 0)
    {
      pam_syslog (pamh, LOG_ERR, "refusing to run in PAM service %s (only gdm-face*)",
                  service ? service : "(none)");
      return PAM_AUTHINFO_UNAVAIL;
    }
  if (pam_get_user (pamh, &user, NULL) != PAM_SUCCESS || !valid_user (user))
    return PAM_USER_UNKNOWN;

  ret = facelock_auth (socket_path, user, timeout, pam_message, pamh, why, sizeof why);
  if (ret == PAM_SUCCESS)
    pam_syslog (pamh, LOG_INFO, "face recognised for %s", user);
  else
    pam_syslog (pamh, LOG_NOTICE, "no face unlock for %s: %s", user, why);
  return ret;
}

PAM_EXTERN int
pam_sm_setcred (pam_handle_t *pamh, int flags, int argc, const char **argv)
{
  return PAM_SUCCESS;
}

#else
/* test client: ./pam_face_test <socket> <user> */
static void
print_message (void *data, const char *text)
{
  printf ("info: %s\n", text);
  fflush (stdout);
}

int
main (int argc, char **argv)
{
  char why[256];
  int ret;

  if (argc != 3 || !valid_user (argv[2]))
    {
      fprintf (stderr, "usage: %s SOCKET USER\n", argv[0]);
      return 2;
    }
  ret = facelock_auth (argv[1], argv[2], 30, print_message, NULL, why, sizeof why);
  printf ("result: %s%s%s\n", ret == PAM_SUCCESS ? "SUCCESS" : ret == PAM_AUTH_ERR ? "AUTH_ERR" : "UNAVAIL",
          ret == PAM_SUCCESS ? "" : " - ", ret == PAM_SUCCESS ? "" : why);
  return ret == PAM_SUCCESS ? 0 : 1;
}
#endif
