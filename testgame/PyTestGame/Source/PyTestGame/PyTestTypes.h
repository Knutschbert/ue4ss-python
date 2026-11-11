#pragma once

#include "CoreMinimal.h"
#include "UObject/Interface.h"
#include "PyTestTypes.generated.h"

// Types used by APyTestActor. Together they carry every property kind the Python bindings handle.

UENUM(BlueprintType)
enum class EPyColor : uint8
{
	Red = 0,
	Green = 1,
	Blue = 2,
};

// An old-style enum stored in a byte property (TEnumAsByte).
UENUM(BlueprintType)
enum EPyMode
{
	PyMode_Idle = 0,
	PyMode_Active = 1,
	PyMode_Done = 5,
};

// An enum with a wide underlying type: an FEnumProperty over int32, not a byte.
UENUM()
enum class EPyWide : int32
{
	Small = 1,
	Big = 100000,
};

USTRUCT(BlueprintType)
struct FPyInner
{
	GENERATED_BODY()

	UPROPERTY() int32 Number = 0;
	UPROPERTY() FString Label;
	UPROPERTY() FName Tag;
	UPROPERTY() TWeakObjectPtr<AActor> Weak;
	UPROPERTY() TArray<int32> Numbers;
	UPROPERTY() TMap<FName, float> Weights;
};

UDELEGATE()
DECLARE_DYNAMIC_DELEGATE_OneParam(FPySingleDelegate, int32, Value);

UDELEGATE()
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FPyMultiDelegate, int32, Value);

UINTERFACE(BlueprintType)
class UPyTestInterface : public UInterface
{
	GENERATED_BODY()
};

class IPyTestInterface
{
	GENERATED_BODY()

public:
	UFUNCTION(BlueprintNativeEvent)
	int32 Ping(int32 Value);
};
