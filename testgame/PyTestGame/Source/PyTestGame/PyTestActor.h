#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "PyTestTypes.h"
#include "PyTestActor.generated.h"

// An actor whose properties cover every kind the Python bindings read and write, filled with known values by Populate().
// Tests find it with find_all_of("PyTestActor") and check the values; mutating tests restore what they change.
UCLASS(BlueprintType)
class PYTESTGAME_API APyTestActor : public AActor, public IPyTestInterface
{
	GENERATED_BODY()

public:
	APyTestActor();

	virtual void BeginPlay() override;

	// Fills every property with its known test value. The game mode calls it after spawning all test actors, so the
	// object references can point at each other.
	UFUNCTION(BlueprintCallable)
	void Populate(const TArray<APyTestActor*>& Others);

	// --- scalars ---
	UPROPERTY() bool bPlain = false;
	UPROPERTY() uint8 bFlagA : 1;
	UPROPERTY() uint8 bFlagB : 1;
	UPROPERTY() int8 Int8Value = 0;
	UPROPERTY() int16 Int16Value = 0;
	UPROPERTY() int32 Int32Value = 0;
	UPROPERTY() int64 Int64Value = 0;
	UPROPERTY() uint8 UInt8Value = 0;
	UPROPERTY() uint16 UInt16Value = 0;
	UPROPERTY() uint32 UInt32Value = 0;
	UPROPERTY() uint64 UInt64Value = 0;
	UPROPERTY() float FloatValue = 0.f;
	UPROPERTY() double DoubleValue = 0.0;

	// --- enums ---
	UPROPERTY() EPyColor Color = EPyColor::Red;
	UPROPERTY() TEnumAsByte<EPyMode> Mode = PyMode_Idle;
	UPROPERTY() EPyWide Wide = EPyWide::Small;

	// --- names and text ---
	UPROPERTY() FName NameValue;
	UPROPERTY() FString StringValue;
	UPROPERTY() FText TextValue;

	// --- object references of every flavour ---
	UPROPERTY() UObject* RawObject = nullptr;
	UPROPERTY() AActor* ActorRef = nullptr;
	UPROPERTY() UClass* ClassRef = nullptr;
	UPROPERTY() TSubclassOf<AActor> SubClass;
	UPROPERTY() TWeakObjectPtr<AActor> WeakActor;
	UPROPERTY() TLazyObjectPtr<AActor> LazyActor;
	UPROPERTY() TSoftObjectPtr<UObject> SoftObject;
	UPROPERTY() TSoftClassPtr<AActor> SoftClass;
	UPROPERTY() TScriptInterface<IPyTestInterface> InterfaceRef;

	// --- structs ---
	UPROPERTY() FVector VectorValue = FVector::ZeroVector;
	UPROPERTY() FPyInner Inner;

	// --- containers ---
	UPROPERTY() TArray<int32> IntArray;
	UPROPERTY() TArray<float> FloatArray;
	UPROPERTY() TArray<bool> BoolArray;
	UPROPERTY() TArray<FString> StringArray;
	UPROPERTY() TArray<FName> NameArray;
	UPROPERTY() TArray<FText> TextArray;
	UPROPERTY() TArray<AActor*> ActorArray;
	UPROPERTY() TArray<FPyInner> StructArray;
	UPROPERTY() TArray<FVector> VectorArray;
	UPROPERTY() TMap<FName, int32> NameIntMap;
	UPROPERTY() TMap<FString, FString> StringMap;
	UPROPERTY() TMap<int32, AActor*> IntActorMap;
	UPROPERTY() TMap<FName, FPyInner> StructMap;
	UPROPERTY() TSet<int32> IntSet;
	UPROPERTY() TSet<FName> NameSet;
	UPROPERTY() TSet<FString> StringSet;
	UPROPERTY() TSet<AActor*> ActorSet;
	UPROPERTY() TMap<TSoftClassPtr<AActor>, int32> SoftClassMap;
	UPROPERTY() TMap<TSoftObjectPtr<UObject>, FString> SoftObjectMap;
	UPROPERTY() TArray<TSoftObjectPtr<UObject>> SoftObjectArray;

	// --- fixed-size arrays ---
	UPROPERTY() int32 FixedInts[4];
	UPROPERTY() FString FixedStrings[2];
	UPROPERTY() FVector FixedVectors[2];
	UPROPERTY() uint8 FixedBytes[3];

	// --- delegates ---
	UPROPERTY() FPySingleDelegate OnSingle;
	UPROPERTY() FPyMultiDelegate OnMulti;

	// --- functions ---
	UFUNCTION(BlueprintCallable) int32 AddInts(int32 A, int32 B);
	UFUNCTION(BlueprintCallable) void SwapInts(UPARAM(ref) int32& A, UPARAM(ref) int32& B);
	UFUNCTION(BlueprintCallable) void SplitVector(const FVector& In, float& X, float& Y, float& Z);
	UFUNCTION(BlueprintCallable) TArray<int32> Range(int32 Count);
	UFUNCTION(BlueprintCallable) FString JoinNames(const TArray<FName>& Names, const FString& Separator);
	UFUNCTION(BlueprintCallable) FPyInner MakeInner(int32 Number, const FString& Label);

	// Output parameters of three kinds (struct, array, string) next to a scalar one, for tests that read and change outputs.
	UFUNCTION(BlueprintCallable) void OutParts(int32 Count, FVector& OutVector, TArray<int32>& OutNumbers, FString& OutText, int32& OutCount);
	UFUNCTION(BlueprintCallable) int32 SumSet(const TSet<int32>& Values);
	UFUNCTION(BlueprintCallable) AActor* PickOther(int32 Index);
	UFUNCTION(BlueprintCallable) void OnHandled(int32 Value);

	// Fires the bound delegates from C++: the engine then runs every bound function (OnHandled here and on the other test actors)
	// through UObject::ProcessEvent without Python being involved.
	UFUNCTION(BlueprintCallable) void Fire(int32 Value);

	// Records the last value delivered through OnSingle / OnMulti, so tests can check that a bound delegate really runs.
	UPROPERTY() int32 LastHandled = 0;
	UPROPERTY() int32 HandledCount = 0;

	// Creates new actors on demand, for tests of object-creation notifications: Count actors that stay, and CountDestroyed
	// actors that are destroyed again before the end of this call (so a notification arrives for an object that is gone).
	UFUNCTION(BlueprintCallable) int32 SpawnMore(int32 Count);
	UFUNCTION(BlueprintCallable) int32 SpawnAndDestroy(int32 CountDestroyed);

	// Spawns one more PyTestActor, populated like the ones the game mode makes (it points at the existing ones), and returns it.
	// For lifetime tests: hold views of its data, destroy it, then check that every view is dead.
	UFUNCTION(BlueprintCallable) APyTestActor* SpawnPopulated();

	// Faults on purpose (writes through a null pointer) to test that a bad call does not take the process down.
	UFUNCTION(BlueprintCallable) void FaultNow();

	// IPyTestInterface
	virtual int32 Ping_Implementation(int32 Value);

private:
	TArray<TWeakObjectPtr<APyTestActor>> KnownOthers;
};
