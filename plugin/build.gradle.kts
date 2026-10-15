plugins {
    `java-library`
    id("com.gradleup.shadow") version "9.6.1"
}

java {
    toolchain { languageVersion = JavaLanguageVersion.of(25) }
}

val velocityVersion = "4.2.0"
val packetEventsVersion = "2.14.0"
val nettyVersion = "4.2.19.Final"

dependencies {
    implementation(project(":proto-java"))
    implementation("com.moandjiezana.toml:toml4j:0.7.2") {
        exclude(group = "com.google.code.gson")
    }

    compileOnly("com.velocitypowered:velocity-api:$velocityVersion")
    annotationProcessor("com.velocitypowered:velocity-api:$velocityVersion")
    compileOnly("com.github.retrooper:packetevents-api:$packetEventsVersion")
    compileOnly("io.netty:netty-buffer:$nettyVersion")

    testImplementation("com.velocitypowered:velocity-api:$velocityVersion")
    testImplementation("com.github.retrooper:packetevents-api:$packetEventsVersion")
    testImplementation("io.netty:netty-buffer:$nettyVersion")
    testImplementation("com.google.code.gson:gson:2.13.2")
    testImplementation(platform("org.junit:junit-bom:5.14.4"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.test {
    useJUnitPlatform()
    maxParallelForks = 1
}

tasks.shadowJar {
    archiveBaseName = "corvus-velocity"
    archiveClassifier = ""
    relocate("com.google.protobuf", "net.onelitefeather.corvus.shaded.protobuf")
    relocate("com.moandjiezana.toml", "net.onelitefeather.corvus.shaded.toml")
}

tasks.build { dependsOn(tasks.shadowJar) }
