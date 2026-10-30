/*
 * pam_facelock_keyring: after a successful face login, hand the user's
 * login password (sealed by the TPM, see facelock-keyring-seal) to the next PAM
 * modules, so pam_gnome_keyring unlocks the login keyring exactly as it
 * does after a password login.
 *
 * Use it in an auth stack *after* a requisite pam_face and before
 * pam_gnome_keyring. It never decides whether authentication succeeds: it
 * always returns PAM_IGNORE.
 */
#define _GNU_SOURCE
#define PAM_SM_AUTH
#include <security/pam_modules.h>
#include <security/pam_ext.h>

#include <ctype.h>
#include <pwd.h>
#include <systemd/sd-login.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <syslog.h>
#include <unistd.h>

#define CRED_DIR "/var/lib/facelock-keyring"
#define SYSTEMD_CREDS "/usr/bin/systemd-creds"
#define MAX_PASSWORD 4096

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

/* Run systemd-creds to unseal the password; returns its length or -1. */
static ssize_t
unseal (const char *path, const char *name, char *buf, size_t size)
{
  int fds[2];
  pid_t pid;
  ssize_t len = 0, n;
  int status;

  if (pipe2 (fds, O_CLOEXEC) < 0)
    return -1;
  pid = fork ();
  if (pid < 0)
    {
      close (fds[0]);
      close (fds[1]);
      return -1;
    }
  if (pid == 0)
    {
      int null = open ("/dev/null", O_RDWR);
      dup2 (null, STDIN_FILENO);
      dup2 (fds[1], STDOUT_FILENO);
      dup2 (null, STDERR_FILENO);
      execl (SYSTEMD_CREDS, "systemd-creds", "decrypt", name, path, "-", (char *) NULL);
      _exit (127);
    }
  close (fds[1]);
  while ((size_t) len < size && (n = read (fds[0], buf + len, size - len)) != 0)
    {
      if (n < 0)
        {
          if (errno == EINTR)
            continue;
          break;
        }
      len += n;
    }
  close (fds[0]);
  while (waitpid (pid, &status, 0) < 0 && errno == EINTR)
    ;
  if (!WIFEXITED (status) || WEXITSTATUS (status) != 0 || (size_t) len >= size)
    return -1;
  return len;
}

/* does the user already have an active session? Then this is the lock
 * screen, and that session's keyring was unlocked at login. (Checking for a
 * running keyring daemon is not enough: it can outlive a logout.) */
static int
has_active_session (const char *user)
{
  struct passwd pw, *res = NULL;
  char buf[4096];

  char **sessions = NULL;
  int found = 0, n;

  if (getpwnam_r (user, &pw, buf, sizeof buf, &res) != 0 || !res)
    return 0;
  n = sd_uid_get_sessions (pw.pw_uid, 0, &sessions);
  for (int i = 0; i < n; i++)
    {
      char *class = NULL, *type = NULL;

      /* the desktop on screen: not the background "manager" session, nor
       * one that is closing after a logout */
      if (!found && sd_session_is_active (sessions[i]) > 0 &&
          sd_session_get_class (sessions[i], &class) >= 0 && strcmp (class, "user") == 0 &&
          sd_session_get_type (sessions[i], &type) >= 0 &&
          (strcmp (type, "wayland") == 0 || strcmp (type, "x11") == 0))
        found = 1;
      free (class);
      free (type);
      free (sessions[i]);
    }
  free (sessions);
  return found;
}

PAM_EXTERN int
pam_sm_authenticate (pam_handle_t *pamh, int flags, int argc, const char **argv)
{
  const char *user = NULL;
  const void *item = NULL;
  char path[256], name[128], password[MAX_PASSWORD + 1];
  struct stat st;
  ssize_t len;

  if (pam_get_user (pamh, &user, NULL) != PAM_SUCCESS || !valid_user (user))
    return PAM_IGNORE;

  /* a password was typed: nothing to add */
  if (pam_get_item (pamh, PAM_AUTHTOK, &item) == PAM_SUCCESS && item)
    return PAM_IGNORE;

  /* the lock screen: the keyring is already open, so skip the TPM, which
   * costs ~0.4 s */
  if (has_active_session (user))
    return PAM_IGNORE;

  snprintf (path, sizeof path, CRED_DIR "/%s.cred", user);
  snprintf (name, sizeof name, "--name=facelock-keyring-%s", user);
  /* only root-owned, private files are trusted */
  if (stat (path, &st) < 0 || st.st_uid != 0 || (st.st_mode & 077))
    return PAM_IGNORE;

  len = unseal (path, name, password, sizeof password);
  if (len <= 0)
    {
      pam_syslog (pamh, LOG_NOTICE, "could not unseal the keyring password for %s", user);
      return PAM_IGNORE;
    }
  password[len] = '\0';
  if (pam_set_item (pamh, PAM_AUTHTOK, password) != PAM_SUCCESS)
    pam_syslog (pamh, LOG_ERR, "could not set the keyring password");
  explicit_bzero (password, sizeof password);
  return PAM_IGNORE;
}

PAM_EXTERN int
pam_sm_setcred (pam_handle_t *pamh, int flags, int argc, const char **argv)
{
  return PAM_IGNORE;
}
