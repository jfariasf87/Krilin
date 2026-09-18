# Android companion

Build requirements: JDK 17, Android SDK platform 35, platform-tools, and network access for the first Gradle build. The checked-in Gradle wrapper pins Gradle 8.10.2; Android Gradle Plugin 8.7.0 and Kotlin 2.0.20 are pinned in the root build file. No Android Studio installation is required.

Set `JAVA_HOME` to the JDK root and `ANDROID_HOME` to the Android SDK root, or create an ignored `android/local.properties` with `sdk.dir` for the SDK. Do not commit machine-specific paths.

From the repository root:

```sh
bash android/gradlew -p android :app:assembleDebug :app:lintDebug
```

Windows PowerShell:

```powershell
.\android\gradlew.bat -p android :app:assembleDebug :app:lintDebug
```

Output: `android/app/build/outputs/apk/debug/app-debug.apk`. This is a debug development APK; no release signing or store publication is configured.

Start an emulator, then use `python -m krilin setup --serial <serial>` from the host environment. Setup requires ADB authorization, installs the companion, provisions its token, and enables it alongside existing accessibility services. The APK also has an Accessibility settings button for manual inspection. Do not replace the entire enabled-service list with only Krilin when configuring manually.

`DemoActivity` is an isolated enter-name/save screen for smoke tests. Clear its task to reset it without stopping the accessibility service:

```sh
adb -s emulator-5554 shell am start -W -f 0x10008000 -n dev.krilin.bridge/.DemoActivity
```

The app requests accessibility tree access and interactive-window/view-ID reporting. It does not request touch exploration or gesture interception. `BridgeService` uses semantic node actions, which are not evidence of physical TalkBack gesture behavior.

To remove the companion, disable Krilin in Accessibility settings and uninstall `dev.krilin.bridge`. Preserve other enabled services. Host configuration and traces are local files in `.local/`; neither is needed in version control.
