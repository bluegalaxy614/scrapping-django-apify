from rest_framework.response import Response
from rest_framework import generics, status as http_status
from django.conf import settings
from django.http import FileResponse
from drf_yasg.utils import swagger_auto_schema
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from auth.utils import is_authenticated
from job.models import UserInfo, Job
from datetime import datetime
import time, tempfile, os, requests, json
from dateutil import parser
from dateutil.relativedelta import relativedelta
from job.utils import execute_gviz_query

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.readonly",
]
creds = ServiceAccountCredentials.from_json_keyfile_name(settings.CREDENTIALS_PATH, scope)
client = gspread.authorize(creds)
service = build('sheets', 'v4', credentials=creds)
drive_service = build("drive", "v3", credentials=creds)
spreadsheet_id = settings.SPREAD_SHEET_ID

jobUrlColumnIndex = settings.JOB_URL_COLUMN_INDEX
lockColumnIndex = settings.LOCK_COLUMN_INDEX
startedAtColumnIndex = settings.STARTED_AT_COLUMN_INDEX
appliedForDateColumnIndex = settings.APPLIED_FOR_DATE_COLUMN_INDEX
problemApplyingColumnIndex = settings.PROBLEM_APPLYING_COLUMN_INDEX


def safe_parse(date_str):
    try:
        formated_date = parser.parse(date_str).replace(tzinfo=None)
        return formated_date.strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return datetime.today().strftime('%Y-%m-%d %H:%M:%S')
    
def get_service_sheet_df():
    sheet = client.open(settings.GOOGLE_SHEET_NAME).get_worksheet_by_id(settings.SHEET_ID)
    data = sheet.get_all_values()
    df = pd.DataFrame(data[1:], columns=data[0])
    df = df.fillna('')
    df['Posted At'] = df['Posted At'].apply(safe_parse)
    posted_before = datetime.today() - relativedelta(days=14)
    posted_before = posted_before.strftime('%Y-%m-%d %H:%M:%S')
    original_df = df.copy()
    df = df[
        (df['AppliedForDate'] == '') &
        (df['Lock'] == '') &
        (df['Easy Apply'] != 'TRUE') &
        (df['Problem Applying'] == '') &
        (df['DateJobRemovedFromSite'] == '') &
        # (df['Posted At'] != '') &
        (df['Posted At'] > posted_before)
    ]
    if len(df) == 0:
        df = original_df[
            (df['AppliedForDate'] == '') &
            (df['Lock'] == '') &
            (df['Easy Apply'] != 'TRUE') &
            (df['Problem Applying'] == '') &
            (df['DateJobRemovedFromSite'] == '')
        ]
    backlog = {}
    skills = df['Skill'].tolist()
    skills = list(set(skills))
    for skill in skills:
        backlog[skill] = len(df[df['Skill'] == skill])
    df.sort_values(by='Priority', ascending=True, inplace=True)
    current_job = df.iloc[0].to_dict()
    job = {
        'jobTitle': current_job['JobTitle'],
        'jobUrl': current_job['JobURL'],
        'jobDescription': current_job['JobDescription'],
        'datePosted': current_job['Posted At'],
        'resume': current_job['Customized Resume']
    }


class GetJobRecordsView(generics.GenericAPIView):
    @swagger_auto_schema(auto_schema=None)
    @is_authenticated
    def post(self, request):
        QUERY = (
            "SELECT A, B, I, N, O "
            "WHERE X != TRUE AND "
            "Q = '' AND "
            "AA = '' AND "
            "AD = '' AND "
            "AF = '' "
            "ORDER BY E "
            "LIMIT 1"
        )
        data = execute_gviz_query(QUERY)
        job = {
            'jobTitle':         data['table']['rows'][0]['c'][0]['v'],
            'jobDescription':   data['table']['rows'][0]['c'][1]['v'],
            'datePosted':       data['table']['rows'][0]['c'][2]['v'],
            'jobUrl':           data['table']['rows'][0]['c'][3]['v'],
            'resume':           data['table']['rows'][0]['c'][4]['v'],
        }

        backlog = {}
        QUERY = "SELECT D "
        data = execute_gviz_query(QUERY)
        skills = data['table']['rows']
        skills = [x['c'][0]['v'] for x in skills]
        unique_skills = list(set(skills))
        for skill in unique_skills:
            backlog[skill] = skills.count(skill)

        leaderboard = []
        userinfos = UserInfo.objects.all()
        for userinfo in userinfos:
            jobs = Job.objects.filter(user=userinfo).all()
            leaderboard.append({
                'name': userinfo.name,
                'email': userinfo.email,
                'num_applied_jobs': len(jobs),
            })

        return Response({'job': job, 'backlog': backlog, 'leaderboard': leaderboard}, status=http_status.HTTP_200_OK)


class JobApplyStartView(generics.GenericAPIView):

    @swagger_auto_schema(auto_schema=None)
    @is_authenticated
    def post(self, request):
        data = request.data
        job_url = data['jobUrl']
        now = int(time.time())

        sheet = client.open(settings.GOOGLE_SHEET_NAME).get_worksheet_by_id(settings.SHEET_ID)
        jobIndex = -1
        QUERY = "SELECT N, AG WHERE AA != '1'"
        data = execute_gviz_query(QUERY)
        for row_index, row in enumerate(data['table']['rows']):
            if row['c'][0]['v'] == job_url:
                jobIndex = int(row['c'][1]['v']) + 1
                print(jobIndex)
                sheet.update_cell(jobIndex, lockColumnIndex, '1')
                sheet.update_cell(jobIndex, startedAtColumnIndex, str(now))
                break
        if jobIndex >= 0:
            return Response({'jobIndex': jobIndex}, status=http_status.HTTP_200_OK)
        else:
            return Response({'msg': 'Selected job not existing or locked'}, status=http_status.HTTP_404_NOT_FOUND)


class JobAppliedView(generics.GenericAPIView):

    @swagger_auto_schema(auto_schema=None)
    @is_authenticated
    def post(self, request):
        data = request.data
        email = data['email']
        job_url = data['jobUrl']
        job_index = data['jobIndex']
        userinfo = UserInfo.objects.filter(email=email).first()
        job = Job.objects.filter(user=userinfo, url=job_url).first()
        if not job:
            job = Job(
                user=userinfo,
                url=job_url,
                applied_at=datetime.now()
            )
            job.save()
        sheet = client.open(settings.GOOGLE_SHEET_NAME).get_worksheet_by_id(settings.SHEET_ID)
        sheet.update_cell(job_index, lockColumnIndex, '')
        sheet.update_cell(job_index, startedAtColumnIndex, '')
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet.update_cell(job_index, appliedForDateColumnIndex, now)
        return Response(status=http_status.HTTP_200_OK)


class JobRejectView(generics.GenericAPIView):

    @swagger_auto_schema(auto_schema=None)
    @is_authenticated
    def post(self, request):
        data = request.data
        reject_reason = data['rejectReason']
        job_url = data['jobUrl']
        sheet = client.open(settings.GOOGLE_SHEET_NAME).get_worksheet_by_id(settings.SHEET_ID)
        QUERY = "SELECT N, AG WHERE AA != '1'"
        data = execute_gviz_query(QUERY)
        for row_index, row in enumerate(data['table']['rows']):
            if row['c'][0]['v'] == job_url:
                job_index = int(row['c'][1]['v']) + 1
                sheet.update_cell(job_index, problemApplyingColumnIndex, reject_reason)
        return Response(status=http_status.HTTP_200_OK)


class DownloadResumeView(generics.GenericAPIView):

    @swagger_auto_schema(auto_schema=None)
    def post(self, request):
        data = request.data
        resume_url = data['resume']
        document_id = resume_url.split('/d/')[1]
        document_id = document_id.split('/edit')[0]

        request = drive_service.files().export_media(
            fileId=document_id,
            mimeType="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
        with open(temp_file.name, "wb") as f:
            f.write(request.execute())
        response = FileResponse(open(temp_file.name, "rb"), as_attachment=True, filename="resume.docx")
        os.unlink(temp_file.name)
        
        return response
