plugins {
    `java-library`
}

base.archivesName = "corvus-api"

java {
    // Plain Java without dependencies, so other Velocity plugins can compile against it (compileOnly).
    toolchain { languageVersion = JavaLanguageVersion.of(25) }
}

tasks.compileJava { options.release = 21 }
tasks.compileTestJava { options.release = 21 }

dependencies {
    testImplementation(platform("org.junit:junit-bom:6.1.2"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.test {
    useJUnitPlatform()
    maxParallelForks = 1
}
