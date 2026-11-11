#include "PyTestGameMode.h"
#include "PyTestActor.h"
#include "Engine/World.h"
#include "UObject/ConstructorHelpers.h"

APyTestGameMode::APyTestGameMode()
{
	static ConstructorHelpers::FClassFinder<AActor> SkySphere(TEXT("/Engine/EngineSky/BP_Sky_Sphere"));
	if (SkySphere.Succeeded()) SkySphereClass = SkySphere.Class;
}

void APyTestGameMode::BeginPlay()
{
	Super::BeginPlay();

	UWorld* World = GetWorld();
	if (!World) return;

	TArray<APyTestActor*> Spawned;
	for (int32 i = 0; i < 3; ++i)
	{
		FActorSpawnParameters Params;
		Params.Name = FName(*FString::Printf(TEXT("PyTestActor_%d"), i));
		APyTestActor* Actor = World->SpawnActor<APyTestActor>(FVector(200.f * i, 0.f, 0.f), FRotator::ZeroRotator, Params);
		if (Actor) Spawned.Add(Actor);
	}
	SpawnedCount = Spawned.Num();
	for (APyTestActor* Actor : Spawned) Actor->Populate(Spawned);
	if (SkySphereClass)
	{
		FActorSpawnParameters SkyParams;
		SkyParams.Name = FName(TEXT("PyTestSky"));
		World->SpawnActor<AActor>(SkySphereClass, FVector(0.f, 400.f, 0.f), FRotator::ZeroRotator, SkyParams);
	}
	UE_LOG(LogTemp, Display, TEXT("PyTestGame: spawned and populated %d test actors"), SpawnedCount);
}
