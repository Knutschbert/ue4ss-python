#include "PyTestActor.h"
#include "Engine/World.h"

APyTestActor::APyTestActor()
{
	PrimaryActorTick.bCanEverTick = false;
	bFlagA = 0;
	bFlagB = 0;
	for (int32 i = 0; i < 4; ++i) FixedInts[i] = 0;
	for (int32 i = 0; i < 3; ++i) FixedBytes[i] = 0;
}

void APyTestActor::BeginPlay()
{
	Super::BeginPlay();
}

void APyTestActor::Populate(const TArray<APyTestActor*>& Others)
{
	KnownOthers.Reset();
	for (APyTestActor* Other : Others) KnownOthers.Add(Other);
	AActor* First = Others.Num() > 0 ? Others[0] : nullptr;
	AActor* Second = Others.Num() > 1 ? Others[1] : nullptr;

	bPlain = true;
	bFlagA = 1;
	bFlagB = 0;
	Int8Value = -8;
	Int16Value = -1600;
	Int32Value = 320000;
	Int64Value = -6400000000000LL;
	UInt8Value = 200;
	UInt16Value = 60000;
	UInt32Value = 4000000000u;
	UInt64Value = 18000000000000000000ull;
	FloatValue = 1.5f;
	DoubleValue = 2.718281828459045;

	Color = EPyColor::Green;
	Mode = PyMode_Active;
	Wide = EPyWide::Big;

	NameValue = FName(TEXT("PyTestName"));
	StringValue = TEXT("py test string");
	TextValue = FText::FromString(TEXT("py test text"));

	RawObject = First;
	ActorRef = Second;
	ClassRef = AActor::StaticClass();
	SubClass = APyTestActor::StaticClass();
	WeakActor = First;
	LazyActor = Second;
	SoftObject = TSoftObjectPtr<UObject>(FSoftObjectPath(TEXT("/Engine/Maps/Entry.Entry")));
	SoftClass = TSoftClassPtr<AActor>(FSoftObjectPath(TEXT("/Script/Engine.Actor")));
	InterfaceRef = this;

	VectorValue = FVector(1.f, 2.f, 3.f);
	Inner.Number = 7;
	Inner.Label = TEXT("inner label");
	Inner.Tag = FName(TEXT("InnerTag"));
	Inner.Weak = First;
	Inner.Numbers = {10, 20, 30};
	Inner.Weights.Add(FName(TEXT("a")), 0.5f);
	Inner.Weights.Add(FName(TEXT("b")), 1.5f);

	IntArray = {3, 1, 4, 1, 5, 9, 2, 6};
	FloatArray = {0.5f, 1.5f, 2.5f};
	BoolArray = {true, false, true};
	StringArray = {TEXT("alpha"), TEXT("beta"), TEXT("gamma")};
	NameArray = {FName(TEXT("NameA")), FName(TEXT("NameB"))};
	TextArray = {FText::FromString(TEXT("text one")), FText::FromString(TEXT("text two"))};
	ActorArray.Reset();
	for (APyTestActor* Other : Others) ActorArray.Add(Other);
	StructArray.Reset();
	for (int32 i = 0; i < 3; ++i)
	{
		FPyInner Item;
		Item.Number = 100 + i;
		Item.Label = FString::Printf(TEXT("item %d"), i);
		Item.Weak = First;
		Item.Numbers = {i, i * 2};
		StructArray.Add(Item);
	}
	VectorArray = {FVector(1, 0, 0), FVector(0, 1, 0)};

	NameIntMap.Reset();
	NameIntMap.Add(FName(TEXT("one")), 1);
	NameIntMap.Add(FName(TEXT("two")), 2);
	NameIntMap.Add(FName(TEXT("three")), 3);
	StringMap.Reset();
	StringMap.Add(TEXT("k1"), TEXT("v1"));
	StringMap.Add(TEXT("k2"), TEXT("v2"));
	IntActorMap.Reset();
	for (int32 i = 0; i < Others.Num(); ++i) IntActorMap.Add(i, Others[i]);
	StructMap.Reset();
	{
		FPyInner Entry;
		Entry.Number = 5;
		Entry.Label = TEXT("map entry");
		StructMap.Add(FName(TEXT("entry")), Entry);
	}
	IntSet = {2, 3, 5, 7, 11};
	NameSet = {FName(TEXT("sa")), FName(TEXT("sb"))};
	StringSet = {TEXT("s1"), TEXT("s2"), TEXT("s3")};
	ActorSet.Reset();
	for (APyTestActor* Other : Others) ActorSet.Add(Other);

	SoftClassMap.Reset();
	SoftClassMap.Add(TSoftClassPtr<AActor>(FSoftObjectPath(TEXT("/Script/Engine.Actor"))), 1);
	SoftClassMap.Add(TSoftClassPtr<AActor>(FSoftObjectPath(TEXT("/Script/Engine.Pawn"))), 2);
	SoftObjectMap.Reset();
	SoftObjectMap.Add(TSoftObjectPtr<UObject>(FSoftObjectPath(TEXT("/Engine/Maps/Entry.Entry"))), TEXT("entry map"));
	SoftObjectArray = {TSoftObjectPtr<UObject>(FSoftObjectPath(TEXT("/Engine/Maps/Entry.Entry"))), TSoftObjectPtr<UObject>(FSoftObjectPath(TEXT("/Game/None.None")))};

	for (int32 i = 0; i < 4; ++i) FixedInts[i] = (i + 1) * 11;
	FixedStrings[0] = TEXT("fixed zero");
	FixedStrings[1] = TEXT("fixed one");
	FixedVectors[0] = FVector(1.f, 2.f, 3.f);
	FixedVectors[1] = FVector(4.f, 5.f, 6.f);
	FixedBytes[0] = 7;
	FixedBytes[1] = 8;
	FixedBytes[2] = 9;

	OnSingle.BindUFunction(this, GET_FUNCTION_NAME_CHECKED(APyTestActor, OnHandled));
	OnMulti.AddDynamic(this, &APyTestActor::OnHandled);
	if (First && First != this) OnMulti.AddDynamic(Cast<APyTestActor>(First), &APyTestActor::OnHandled);
}

int32 APyTestActor::AddInts(int32 A, int32 B)
{
	return A + B;
}

void APyTestActor::SwapInts(int32& A, int32& B)
{
	Swap(A, B);
}

void APyTestActor::SplitVector(const FVector& In, float& X, float& Y, float& Z)
{
	X = In.X;
	Y = In.Y;
	Z = In.Z;
}

TArray<int32> APyTestActor::Range(int32 Count)
{
	TArray<int32> Result;
	for (int32 i = 0; i < Count; ++i) Result.Add(i);
	return Result;
}

FString APyTestActor::JoinNames(const TArray<FName>& Names, const FString& Separator)
{
	FString Result;
	for (int32 i = 0; i < Names.Num(); ++i)
	{
		if (i) Result += Separator;
		Result += Names[i].ToString();
	}
	return Result;
}

FPyInner APyTestActor::MakeInner(int32 Number, const FString& Label)
{
	FPyInner Result;
	Result.Number = Number;
	Result.Label = Label;
	return Result;
}

void APyTestActor::OutParts(int32 Count, FVector& OutVector, TArray<int32>& OutNumbers, FString& OutText, int32& OutCount)
{
	OutVector = FVector(Count, Count * 2, Count * 3);
	OutNumbers.Reset();
	for (int32 i = 0; i < Count; ++i) OutNumbers.Add(i * i);
	OutText = FString::Printf(TEXT("parts %d"), Count);
	OutCount = Count;
}

int32 APyTestActor::SumSet(const TSet<int32>& Values)
{
	int32 Sum = 0;
	for (int32 Value : Values) Sum += Value;
	return Sum;
}

AActor* APyTestActor::PickOther(int32 Index)
{
	return KnownOthers.IsValidIndex(Index) ? KnownOthers[Index].Get() : nullptr;
}

void APyTestActor::OnHandled(int32 Value)
{
	LastHandled = Value;
	++HandledCount;
}

int32 APyTestActor::SpawnMore(int32 Count)
{
	UWorld* World = GetWorld();
	if (!World) return 0;
	int32 Spawned = 0;
	for (int32 i = 0; i < Count; ++i)
	{
		if (World->SpawnActor<AActor>(AActor::StaticClass(), FVector(0.f, 100.f * i, 50.f), FRotator::ZeroRotator)) ++Spawned;
	}
	return Spawned;
}

int32 APyTestActor::SpawnAndDestroy(int32 CountDestroyed)
{
	UWorld* World = GetWorld();
	if (!World) return 0;
	int32 Destroyed = 0;
	for (int32 i = 0; i < CountDestroyed; ++i)
	{
		if (AActor* Temporary = World->SpawnActor<AActor>(AActor::StaticClass(), FVector(0.f, -100.f * i, 50.f), FRotator::ZeroRotator))
		{
			Temporary->Destroy();
			++Destroyed;
		}
	}
	return Destroyed;
}

APyTestActor* APyTestActor::SpawnPopulated()
{
	UWorld* World = GetWorld();
	if (!World) return nullptr;
	APyTestActor* Fresh = World->SpawnActor<APyTestActor>(FVector(0.f, 0.f, 200.f), FRotator::ZeroRotator);
	if (!Fresh) return nullptr;
	TArray<APyTestActor*> Others;
	Others.Add(this);
	for (const TWeakObjectPtr<APyTestActor>& Known : KnownOthers)
	{
		if (APyTestActor* Other = Known.Get()) Others.AddUnique(Other);
	}
	Fresh->Populate(Others);
	return Fresh;
}

void APyTestActor::Fire(int32 Value)
{
	OnSingle.ExecuteIfBound(Value);
	OnMulti.Broadcast(Value);
}

void APyTestActor::FaultNow()
{
	volatile int32* Nothing = nullptr;
	*Nothing = 42;
}

int32 APyTestActor::Ping_Implementation(int32 Value)
{
	return Value + 1;
}
