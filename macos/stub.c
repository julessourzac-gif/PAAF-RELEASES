/*
 * Exécutable de PAAF.app (Contents/MacOS/PAAF).
 * Un vrai binaire, pour que l'application se signe avec le « hardened runtime » et passe la
 * notarisation Apple : il exécute simplement le lanceur bash rangé dans Contents/Resources,
 * qui installe l'environnement Python au premier lancement puis ouvre l'interface.
 *
 *   clang -O2 -arch arm64 -arch x86_64 -mmacosx-version-min=11.0 -o PAAF macos/stub.c
 */
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
    char exe[PATH_MAX], real[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof(exe);
    if (_NSGetExecutablePath(exe, &size) != 0 || realpath(exe, real) == NULL) {
        fprintf(stderr, "PAAF : chemin de l'application introuvable\n");
        return 1;
    }
    /* .../PAAF.app/Contents/MacOS/PAAF -> .../PAAF.app/Contents/Resources/launcher */
    char *macos = dirname(real);
    snprintf(script, sizeof(script), "%s/../Resources/launcher", macos);

    /* arguments transmis tels quels (fichiers déposés, etc.) */
    char **args = calloc((size_t)argc + 2, sizeof(char *));
    if (args == NULL) return 1;
    args[0] = "/bin/bash";
    args[1] = script;
    for (int i = 1; i < argc; i++) args[i + 1] = argv[i];
    execv("/bin/bash", args);
    perror("PAAF : lancement impossible");
    return 1;
}
