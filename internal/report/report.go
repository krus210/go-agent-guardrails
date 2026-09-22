package report

import "context"

type Record struct {
	Revision int
	ID       int
	Region   string
}

type View struct {
	ID     int
	Region string
}

func ToView(record Record) View {
	return View{ID: record.ID, Region: record.Region}
}

// Forward sends one value or stops on cancellation. The caller owns out.
func Forward(ctx context.Context, out chan<- int, value int) <-chan struct{} {
	done := make(chan struct{})
	go func() {
		defer close(done)
		select {
		case out <- value:
		case <-ctx.Done():
		}
	}()
	return done
}
