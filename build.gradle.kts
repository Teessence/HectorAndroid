// Top-level build file. Plugins are put on the buildscript classpath here so the
// app module can apply them by id without repeating versions (this is also the
// most reliable way to pull in the Chaquopy plugin).
buildscript {
    repositories {
        google()
        mavenCentral()
    }
    dependencies {
        classpath("com.android.tools.build:gradle:8.5.2")
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:1.9.24")
        classpath("com.chaquo.python:gradle:16.0.0")
    }
}
