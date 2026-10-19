
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>

static void show_error(const wchar_t *message) {
    MessageBoxW(NULL, message, L"Booster Robot Manager", MB_ICONERROR | MB_OK);
}

int main(void) {
    wchar_t exe_path[MAX_PATH];
    DWORD length = GetModuleFileNameW(NULL, exe_path, MAX_PATH);
    if (length == 0 || length >= MAX_PATH) {
        show_error(L"无法获取程序路径。");
        return 1;
    }
    for (DWORD i = length; i > 0; --i) {
        if (exe_path[i - 1] == L'\\' || exe_path[i - 1] == L'/') {
            exe_path[i - 1] = 0;
            break;
        }
    }

    wchar_t python_path[MAX_PATH * 2];
    _snwprintf(python_path, MAX_PATH * 2, L"%ls\\python\\pythonw.exe", exe_path);
    if (GetFileAttributesW(python_path) == INVALID_FILE_ATTRIBUTES) {
        show_error(L"未找到 python\\pythonw.exe，请确认整个程序文件夹已完整解压。");
        return 1;
    }

    wchar_t command[MAX_PATH * 3];
    _snwprintf(command, MAX_PATH * 3, L"\"%ls\" -m brm", python_path);
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    ZeroMemory(&startup, sizeof(startup));
    startup.cb = sizeof(startup);
    ZeroMemory(&process, sizeof(process));

    BOOL ok = CreateProcessW(
        python_path, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL,
        exe_path, &startup, &process
    );
    if (!ok) {
        wchar_t message[512];
        _snwprintf(message, 512, L"启动失败（错误码 %lu）。请查看 README-Windows.txt。",
                   (unsigned long)GetLastError());
        show_error(message);
        return 1;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
