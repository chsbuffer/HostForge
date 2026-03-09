#ifdef _WIN32
#define EXPORTS __declspec(dllexport)
#elif __GNUC__
#define EXPORTS __attribute__((visibility("default")))
#else
#define EXPORTS
#endif

extern "C" {


EXPORTS int plus(int a, int b){
    return a + b;
}

}
