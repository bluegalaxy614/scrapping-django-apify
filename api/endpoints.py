import logging
import pdb

import requests
from apify_client import ApifyClient
from django.conf import settings
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response


from scrapping import models


logger = logging.getLogger(__name__)



class ScrapeZipRecruiterEndpoint(APIView):
    
    permission_classes = []
    authentication_classes = []

    @swagger_auto_schema(
        tags=["Scraping"],
        operation_description="Scrape ZipRecuriter for jobs data",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["NumberOfDays", "AppConsuming"],
            properties={
                "NumberOfDays": openapi.Schema(
                    type=openapi.TYPE_INTEGER,
                    description="The number of days to scrape job data for"
                ),
                "AppConsuming": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="The consuming app's identifier"
                ),
            },
        )
    )
    def post(self, request):
        number_of_days = request.data.get("NumberOfDays")
        app_consuming = request.data.get("AppConsuming")

        if not number_of_days:
            return Response({"error": "NumberOfDays is required"}, status=400)
        
        if not app_consuming:
            return Response({"error" : "AppConsuming is required"}, status=status.HTTP_400_error)

        client = ApifyClient(settings.APIFY_KEYS.get("ziprecruiter_key"))

        configs = models.ConfigZipRecruiter.objects.all()
        results = []
        for config in configs:

            scrape_url = config.url.replace("days=1", "days=%s" % number_of_days)
            priority = config.priority
            skill = config.skill

            #############################################################
            # Create the history first record for this scrape
            #############################################################

            scrape_history = models.JobBoardScrapeHistory.objects.create(
                url=scrape_url,
                days=number_of_days,
                job_board="ziprecruiter",
                skill=skill,
                priority=priority
            )
            
            #############################################################
            # Run the scraper via apify
            #############################################################

            run_input = {
				"startUrls": [{ "url": scrape_url}],
				"proxy": {
					"useApifyProxy": True,
					"apifyProxyGroups": ["RESIDENTIAL"],
					"apifyProxyCountry": "US",
				},
			}
            result = client.actor("memo23/apify-ziprecruiter-scraper").call(run_input=run_input)           
            
            #############################################################
            # Store the results, and store the raw json so we can inspect
            # and see what we need from the response
            #############################################################

            # clean some values to timestamps
            for i in ["startedAt", "createdAt", "notifiedAboutChangeAt"]:
                result["pricingInfo"][i] = result["pricingInfo"][i].timestamp()
            result["finishedAt"] = result["finishedAt"].timestamp()
            result["startedAt"] = result["startedAt"].timestamp()

            run_price = result.get("usageTotalUsd")
            dataset_id = result.get("defaultDatasetId")
            dataset_results = [x for x in client.dataset(dataset_id).iterate_items()]

            scrape_history.date_scrape_ended = timezone.now()
            scrape_history.raw_json_response_from_apify = result
            scrape_history.run_id = dataset_id
            scrape_history.days = number_of_days
            scrape_history.ending_state = "ended"
            scrape_history.ending_state_set_by = "[APIView] - end of scrape"
            scrape_history.number_of_jobs_returned = len(dataset_results)
            scrape_history.price = run_price
            scrape_history.save()
            
            ################################
            # TO DO:
            #   Store each result and link
            #   to scrape history result
            ################################

            #if len(dataset_results) > 0:
            #    self.store_results(scrape_history, dataset_results)

            results.append({
                "job_board" : "ziprecruiter",
                "scrape_url": scrape_url,
                "run_metadata" : result,
                "results" : dataset_results
            })

        return Response(
            {"message": "Scraping completed", "results": results},
            status=status.HTTP_200_OK
        )


    def store_results(self, scrape_history, results):
        raise NotImplementedError()
